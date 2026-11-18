# DRINQO Frontend API Integration Handoff

Backend route and payload handoff for the frontend team. This document describes
the API routes implemented in the backend workspace; it does not confirm that
they have been deployed to a server.

Payment source update: [Razorpay payments](./docs/razorpay-payments.md) supersedes
older payment limitations below. The integration is disabled by default and
restricted to Test Mode; migration `0010_payment_ledger.py` is unapplied and
tests have not been run for this change. It is not live payment activation.
Display **Platform fee** using the additive `platform_fee` alias; legacy
`application_fee` is the same ₹50 charge, not a second fee. No delivery charge
is added. The existing OTP source contract is in
[Staff-confirmed handover](./docs/final-auth-handover.md); payment now adds a
verified-payment gate before preparation and handover.

## Environment and API documentation

Set the API origin from the environment configuration supplied by the backend
team. The repository does not specify a deployed development, staging, or
production domain.

```text
API_ORIGIN=<environment API origin>
```

Available API documentation endpoints:

- `GET /api/schema/` — OpenAPI schema
- `GET /api/docs/` — Swagger UI

The complete endpoint payload examples and validation details are in
[SHOP_ADMIN_API_CONTRACT.md](./SHOP_ADMIN_API_CONTRACT.md) and
[SUPER_ADMIN_API_CONTRACT.md](./SUPER_ADMIN_API_CONTRACT.md). The Google Sign-In,
account-linking, and Django JWT contract is in
[GOOGLE_AUTH_SETUP.md](./GOOGLE_AUTH_SETUP.md). Use
this handoff as the quick route index; use those contracts for exact response
fields.

## Authentication

Protected API calls use a Django JWT access token:

```http
Authorization: Bearer <Django access JWT>
```

Obtain a Django token pair using one of these backend flows:

- `POST /api/accounts/login/` with email and password.
- `POST /api/accounts/google-login/` with a Google ID token.

Use the returned Django access token for protected calls and refresh it through
`POST /api/accounts/token/refresh/`. Logout through
`POST /api/accounts/logout/` to blacklist the refresh token. Call
`GET /api/accounts/me/` to read the authenticated account and its `user_type`.
The supported roles are `customer`, `shop_admin`, and `super_admin`; the
backend remains authoritative for role access. Do not put Firebase
service-account credentials, Google Client Secrets, Django secrets, or JWT
signing keys in the frontend. Legacy push-token registration is separate from
API authentication; push delivery is currently disabled.

## Common request/response conventions

- JSON requests: `Content-Type: application/json`.
- File uploads: `multipart/form-data`; let the HTTP client set the multipart
  boundary.
- Successful requests generally return `200`; resource creation commonly
  returns `201`; deletes may return `204` with no response body.
- Validation errors return `400`; missing/invalid authentication returns
  `401`; role/access failures return `403`; inaccessible or missing records
  commonly return `404`.
- Paginated lists use `count`, `next`, `previous`, and `results`. Where
  supported, use `page` and `page_size`.
- Monetary values are serialized as decimal strings. Parse/format them as
  currency in the UI; avoid floating-point arithmetic for totals.
- IDs in path examples below are placeholders. Replace `{id}` with the
  returned record ID and URL-encode query values.

## Customer API routes

All paths are under `/api/customer/`.

| Method | Path | Frontend use |
| --- | --- | --- |
| GET, PATCH | `/profile/` | Read/update customer profile. |
| GET, POST | `/vehicles/` | List/create customer vehicles. |
| GET, PATCH, DELETE | `/vehicles/{id}/` | Read/update/delete a vehicle. |
| GET, POST | `/favorites/shops/` | List/add favorite shops. |
| DELETE | `/favorites/shops/{id}/` | Remove a favorite shop. |
| GET | `/cart/` | Read cart and totals. |
| POST | `/cart/items/` | Add an item to the cart. |
| PATCH, DELETE | `/cart/items/{id}/` or `/cart/items/{id}/delete/` | Update/remove a cart item. |
| POST | `/checkout/preview/` | Preview checkout totals before placing an order. |
| GET | `/notifications/` | List customer notifications. |
| POST | `/notifications/{id}/read/` | Mark one notification read. |
| POST | `/notifications/read-all/` | Mark all customer notifications read. |
| GET, PATCH | `/notifications/preferences/` | Read/update notification preferences. |
| POST, DELETE | `/push-token/` | Register/remove a legacy push token; delivery is disabled. |
| GET, POST | `/verification/sessions/` | List/create document verification sessions. |
| GET | `/verification/sessions/{id}/` | Read a verification session's status. |
| GET, POST | `/support/tickets/` | List/create support tickets. |
| GET | `/support/tickets/{id}/` | Read a ticket's details/status. |
| GET, POST | `/account/deletion-request/` | Read latest request/create a deletion request. |
| POST | `/account/deletion-request/cancel/` | Cancel an eligible deletion request. |
| GET | `/legal/policies/` | List published legal policies. |

### Customer request notes

- Verification creation is `multipart/form-data` with `document` and
  `document_type`. PDF/JPG/JPEG/PNG are supported; maximum file size is 10 MB.
- Support ticket creation uses JSON `subject` and `message`.
- Legacy push-token registration uses JSON `token` and `platform`; DELETE
  removes the authenticated user's matching token. Push delivery is disabled,
  so registration does not enable notifications.
- Notification preference fields are `push_enabled`, `email_enabled`,
  `order_updates_enabled`, and `promotions_enabled`.
- Deletion requests are tracked with a 30-day cancellation window. The backend
  currently does not execute scheduled account erasure.

### Age verification

`POST /api/accounts/age-verification/` remains available with the existing
`{"date_of_birth":"YYYY-MM-DD"}` request. It records a self-reported DOB only;
it does not grant trusted age verification. The response retains
`date_of_birth`, `age_verified`, and `age_verified_at`, and adds
`age_verification_status` (`proof_required`, `pending`, `approved`, or
`rejected`).

To submit age proof, upload a document using
`POST /api/customer/verification/sessions/` as multipart form data with
`document_type=age_proof` and `document=<file>`. Checkout preview and order
creation remain unavailable until a Super Admin reviews that proof, confirms a
DOB of at least 21 years, and the backend reports `age_verified: true`.
Continue reading the user's verification-session status to update the UI.
The DOB on the account profile is read-only; use the age-verification endpoint
to submit or update a self-reported DOB.

## Customer checkout and orders

Checkout and order routes require a Django JWT and a customer account.
The backend derives the customer and selected shop from the authenticated user
and server-side cart; clients must not send customer/shop IDs or price totals.

Both checkout preview and order creation require the customer's current
latitude and longitude. The server compares those coordinates with the cart
shop's saved GPS coordinates and enforces a 100-meter radius. Shop coordinates
must be configured or checkout fails closed. Client-provided GPS is an
assertion from the app and can be spoofed; the backend does not attest that
the device is physically at those coordinates.

| Method | Path | Frontend use |
| --- | --- | --- |
| POST | `/api/customer/checkout/preview/` | Calculate checkout eligibility and server-side totals. |
| POST | `/api/orders/create/` | Create an order from the authenticated customer's server-side cart. |
| GET | `/api/orders/history/` | List the authenticated customer's orders (paginated). |
| GET | `/api/orders/{order_id}/` | Read an owned order and its items. |
| POST | `/api/orders/{order_id}/cancel/` | Cancel an eligible order; no body is required. |
| GET | `/api/orders/{order_id}/payment-status/` | Read payment method/status and amounts. |
| POST | `/api/orders/{order_id}/payment/initiate/` | Initialize Test Mode payment after Shop confirmation and required approval. |
| POST | `/api/orders/{order_id}/payment/verify/` | Verify checkout identifiers/signature and captured payment on the backend. |
| POST | `/api/orders/{order_id}/payment/reconcile/` | Recover an existing payment's provider state after interruption. |
| GET, POST | `/api/orders/{order_id}/handover-otp/` | Retired compatibility routes; return HTTP 410. |
| POST | `/api/orders/{order_id}/handover-otp/reissue/` | Retired compatibility route; returns HTTP 410. |
| GET | `/api/orders/{order_id}/tracking/` | Read order status and tracking steps. |
| GET | `/api/orders/{order_id}/live-location/` | Read current location for an owned order. |

Preview and create request examples:

```json
{
  "pickup_mode": "walk_in",
  "latitude": 18.520430,
  "longitude": 73.856744
}
```

```json
{
  "pickup_mode": "vehicle",
  "vehicle_id": 42,
  "latitude": 18.520430,
  "longitude": 73.856744
}
```

The vehicle must belong to the authenticated customer. Coordinates are
required, numeric, and bounded to latitude `-90..90` and longitude `-180..180`.
The 100-meter distance is measured from the shop location to the submitted
coordinates using great-circle distance; exactly 100 meters is allowed.

Preview always calculates amounts from the current cart prices. It returns
`can_checkout`, `subtotal`, `application_fee`, `platform_fee`, `total_amount`,
`minimum_order_amount`, `distance_to_shop_m`, and `pickup_radius_m`. A product
subtotal below ₹1,000 or a customer outside the radius returns
`can_checkout: false` with a human-readable `detail`; the fee is ₹50 and is
not included in the minimum subtotal. Order creation recalculates all values
and returns `400` if the subtotal or distance requirement fails. Client
`subtotal`/`total_amount` values, if sent, are ignored.

Order create/detail/history responses include `id`, `order_number`, `shop`,
`status`, `payment_method`, `payment_status`, `pickup_mode`, `vehicle`,
`vehicle_details`, `subtotal`, `application_fee`, `platform_fee`, `total_amount`, `items`,
`created_at`, and `updated_at`. `vehicle_details` is null or contains
`id`, `vehicle_type`, `vehicle_number`, and `vehicle_name`. Each order item
contains `id`, `product`, `product_name`, `unit_price`, `quantity`, and
`subtotal`. Monetary fields are decimal strings.

Order cancellation returns the updated order on success. A cancellation that
conflicts with the current order state returns `409` and a `detail`. Attempts
to access another customer's order return `404`. The tracked order states are
`pending`, `pending_super_admin`, `admin_approved`, `accepted`, `confirmed`,
`preparing`, `ready`, `completed`, `rejected`, and `cancelled`.
The minimum product subtotal is ₹1,000 before fees; the ₹50 Platform fee
is separate and is exposed under both fee aliases. Orders whose `total_amount` meets the persisted high-value
threshold (currently ₹15,000) require Super Admin approval, with the fee
included in `total_amount`.

## Shop Admin API routes

All paths are under `/api/shop-admin/`.

| Method | Path | Frontend use |
| --- | --- | --- |
| GET | `/me/` | Read authenticated Shop Admin and assigned shop. |
| GET, PATCH | `/shop/` | Read/update shop details. |
| PATCH | `/shop/availability/` | Update shop availability. |
| GET | `/dashboard/` | Read Shop Admin dashboard data. |
| GET | `/notifications/` | List Shop Admin notifications. |
| POST | `/notifications/{id}/read/` | Mark one notification read. |
| POST | `/notifications/read-all/` | Mark all Shop Admin notifications read. |
| GET, PATCH | `/notifications/preferences/` | Read/update Shop Admin notification preferences. |
| POST, DELETE | `/push-token/` | Register/remove a legacy push token; delivery is disabled. |
| GET, POST | `/products/` | List/create products. |
| GET, PATCH, DELETE | `/products/{id}/` | Read/update/delete a product. |
| POST, DELETE | `/products/{id}/image/` | Upload/remove a product image. |
| POST | `/products/bulk-import/` | Import products from CSV. |
| GET | `/inventory/` | List shop inventory. |
| POST | `/inventory/{product_id}/adjust/` | Adjust product stock. |
| GET | `/orders/` | List Shop Admin orders. |
| GET | `/orders/{order_id}/` | Read an order. |
| POST | `/orders/{order_id}/accept/` | Accept an order. |
| POST | `/orders/{order_id}/reject/` | Reject an order. |
| POST | `/orders/{order_id}/complete/` | Retired compatibility route; use staff-confirmed handover. |
| GET | `/reports/?range=7d` | Read shop reports; range accepts `7d`, `30d`, `90d`. |
| GET | `/licenses/` | List legacy Shop Admin license records, if linked. |
| GET | `/licenses/{document_id}/` | Read legacy license metadata. |
| GET | `/licenses/{document_id}/download/` | Download a legacy license file. |
| GET, POST | `/application/` | Read or submit/resubmit the authenticated Shop Admin's own application. |
| GET | `/application/documents/{document_id}/` | Read an active application license document. |
| GET | `/application/documents/{document_id}/download/` | Download an active application license document. |
| POST | `/onboarding/` | Shop Admin onboarding/application submission. |

### Shop Admin request notes

- Product image upload uses multipart field `image`; maximum size is 5 MB.
- Bulk import uses multipart field `file`, with CSV headers
  `name,category,price,stock_quantity`; `brand` and `description` are optional.
  Limit: 2 MB and 500 data rows. Invalid rows prevent partial creation.
- Reports accept `range=7d`, `range=30d`, or `range=90d`.
- `/licenses/` refers to legacy license records. For current onboarding
  documents use `/application/` and `/application/documents/...`.
- A correction request puts the application in the existing `rejected` state;
  `GET /application/` includes `review_history` with correction/rejection
  reasons and notes. Resubmit using `POST /application/` with all required
  onboarding fields and a replacement license document as multipart form
  data. Success returns the application to `pending` for Super Admin re-review.
- Push-token APIs store device tokens; they do not send push notifications.
- Django password reset endpoints are
  `POST /api/accounts/password-reset/` and
  `POST /api/accounts/password-reset/confirm/`.

## Super Admin API routes

All paths are under `/api/super-admin/`.

| Method | Path | Frontend use |
| --- | --- | --- |
| GET | `/me/` | Read Super Admin identity. |
| GET | `/dashboard/` | Read platform dashboard. |
| GET | `/notifications/` | List Super Admin notifications. |
| POST | `/notifications/{id}/read/` | Mark one notification read. |
| GET | `/shop-applications/` | List shop applications. |
| GET | `/shop-applications/{id}/` | Read application detail. |
| POST | `/shop-applications/{id}/approve/` | Approve application. |
| POST | `/shop-applications/{id}/reject/` | Reject application. |
| POST | `/shop-applications/{id}/request-changes/` | Request application changes; moves it to `rejected` until the owner resubmits. |
| GET | `/shops/` | List shops. |
| GET | `/shops/{id}/` | Read shop detail. |
| PATCH | `/shops/{id}/status/` | Activate/suspend a shop. |
| GET | `/customers/` | List customers. |
| GET | `/customers/{id}/` | Read customer detail. |
| PATCH | `/customers/{id}/status/` | Block/unblock customer. |
| GET | `/high-value-orders/` | List high-value orders. |
| GET | `/high-value-orders/{id}/` | Read high-value order detail. |
| POST | `/orders/{id}/approval/` | Approve/reject high-value order. |
| GET | `/payments/` | List order-derived payment status records. |
| GET | `/reports/` | Read platform reports. |

The Super Admin application detail's `status_history` includes audit metadata,
including request-changes/rejection reasons and notes.
| GET | `/audit-logs/` | Read audit logs. |
| GET, POST | `/verification-sessions/` | List/review customer verification sessions. |
| GET | `/verification-sessions/{session_id}/document/` | Download a private customer proof document for Super Admin review. |
| GET | `/support-tickets/` | List customer support tickets. |
| PATCH | `/support-tickets/{id}/` | Update ticket status/respond. |
| GET, POST | `/legal-policies/` | List/create legal policies. |
| PATCH | `/legal-policies/{id}/` | Update legal policy. |
| GET, PATCH | `/settings/` | Read/update high-value order threshold and nearby-shop search radius. |

Super Admin settings include `nearby_shop_radius_km` (default `10.00`, valid
range `0.10`–`100.00`). PATCH it with
`{"nearby_shop_radius_km":"25.00"}`. This changes the radius used by
`GET /api/shop/nearby/`; the nearby response includes the active `radius_km`.

## Order live location

These order routes are under `/api/orders/`:

| Method | Path | Frontend use |
| --- | --- | --- |
| POST | `/{order_id}/live-location/` | Owning Shop Admin starts/updates/stops location sharing. |
| GET | `/{order_id}/live-location/` | Order customer or owning Shop Admin reads latest location. |

Both methods require a valid Django access JWT in
`Authorization: Bearer <Django access JWT>`. GET is allowed only to the order's
customer or the owning Shop Admin. POST is allowed only to the owning Shop
Admin; customers cannot publish location. A customer or Shop Admin attempting
to access another user's/shop's order receives `404`; an unsupported role
receives `403`; missing/invalid authentication receives `401`.

POST starts or updates sharing with all three fields:

```json
{"latitude":19.076000,"longitude":72.877700,"is_sharing":true}
```

Coordinates must be within latitude `-90..90` and longitude `-180..180`.
Location publishing is supported only for `vehicle` pickup orders in
`confirmed`, `preparing`, or `ready` status; other pickup modes/statuses return
`400`. Stop sharing with `{"is_sharing":false}` and omit both coordinates.
Supplying coordinates while stopping, omitting `is_sharing`, or omitting a
coordinate while starting returns `400`. Stop clears the stored coordinates.

GET returns this direct-root response shape (coordinates are JSON numbers):

```json
{
  "order_id": 123,
  "is_sharing": true,
  "latitude": 19.076,
  "longitude": 72.8777,
  "updated_at": "2026-10-27T13:00:00Z"
}
```

`updated_at` is the latest location-record update time in UTC ISO-8601 format,
or `null` when no record exists. `is_sharing` is false and both coordinates
are `null` if sharing is stopped, no coordinates exist, the order is not
eligible, or the location is older than 60 seconds. A location exactly 60
seconds old is not stale; one older than 60 seconds is stale. Stale coordinates
are cleared when GET detects them. Clients can poll GET; WebSocket push is not
provided. The backend handover OTP source is described in
[Vehicle handover OTP](./docs/phase-2a-handover-otp.md). Its new verified-payment
requirement is described in [Razorpay payments](./docs/razorpay-payments.md).

## Not currently available end-to-end

- Production/development API domain: obtain the correct `API_ORIGIN` from the
  backend/deployment team.
- Push delivery is disabled. Notification records remain available in-app;
  legacy push-token registration is retained but tokens are not sent to a
  provider.
- Customer document verification provider/callback: current review is manual.
- Scheduled account deletion/anonymization worker.
- Live payment collection and shop settlement: Test Mode source and a separate
  payment ledger now exist, but merchant/Route approval, reviewed migration,
  isolated tests, account mapping and Flutter integration remain outstanding.
  The Super Admin payments list remains an `Order` summary rather than a
  gateway transaction-reference API. See [Razorpay payments](./docs/razorpay-payments.md).
- Handover OTP source exists without an SMS provider; deployment and isolated
  regression verification remain outstanding. See
  [Vehicle handover OTP](./docs/phase-2a-handover-otp.md).
- Flutter/mobile integration and real-device verification have not been
  confirmed by this backend handoff.

## Detailed contract references

- [Shop Admin and Customer contracts](./SHOP_ADMIN_API_CONTRACT.md)
- [Super Admin contract](./SUPER_ADMIN_API_CONTRACT.md)
- [Razorpay Test Mode payments and Platform fee](./docs/razorpay-payments.md)
- [Vehicle handover OTP](./docs/phase-2a-handover-otp.md)
