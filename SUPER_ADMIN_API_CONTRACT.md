# DRINQO Super Admin API Contract

Verified against the backend source in this workspace on 2026-10-23. This document describes implemented paths and response shapes; example values are illustrative, while field names and types reflect the current code. Do not treat explicitly missing endpoints as frontend-ready.

Payment source update: [Razorpay payments](./docs/razorpay-payments.md) documents
the separate ledger and disabled-by-default Test Mode APIs. Migration
`0010_payment_ledger.py` is unapplied, tests for this change have not run, and
live payments/Route settlement are not activated. Existing order summaries add
`platform_fee` as an alias for `application_fee`; display a single ₹50
**Platform fee**. No additional delivery charge is introduced. Super Admin
approval remains separate from captured payment and Shop Admin OTP handover.

## Base URL and authentication

The URL prefix is wired in `config/urls.py`:

```python
path("api/super-admin/", include("shop_admin.super_admin_urls")),
```

Use a Django JWT access token belonging to an active user whose
`user_type` is `super_admin`:

```http
Authorization: Bearer <Django access JWT>
```

Do not store passwords, Firebase service-account credentials, or signing secrets
in frontend source.

### Common errors

| Status | Actual meaning/response |
|---|---|
| `400` | Validation error, usually `{"field":"message"}` or `{"detail":"message"}`. Invalid `page_size` is `{"page_size":"Page size must be between 1 and 100."}`. |
| `401` | Missing, invalid, expired, or inactive-user JWT credentials. Missing credentials return `{"detail":"Authentication credentials were not provided."}`. |
| `403` | Authenticated non-admin: `{"detail":"Super Admin access required."}`. |
| `404` | Missing object: `{"detail":"Not found."}`. A route not present in the URL configuration is not implemented and returns Django's route-level 404 (which may not have this JSON shape). |
| `405` | Route exists but does not allow the requested method; for example, shop approval routes are POST, not PATCH. |

Implemented Super Admin API views use Django JWT authentication and the role
permission (with the relevant HTTP method):

```python
@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def endpoint(request):
    ...
```

The role permission is defined in `shop_admin/super_admin_views.py` and checks `request.user.user_type == "super_admin"`.

## Pagination contract

List endpoints that use `_paginated_response` accept `page` (default `1`) and `page_size` (default `20`, range `1–100`). Their response envelope is:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": []
}
```

`next` and `previous` are absolute URL strings or `null`. A page beyond available results returns `400`, not an empty page.

## Endpoint summary

| Requested function | Implemented method and path | Status |
|---|---|---|
| Admin identity | `GET /api/super-admin/me/` | Implemented |
| Dashboard | `GET /api/super-admin/dashboard/` | Implemented |
| Notifications list/read | `GET /api/super-admin/notifications/`, `POST /api/super-admin/notifications/{notification_id}/read/` | Implemented |
| Shop approvals list | `GET /api/super-admin/shop-applications/` | Implemented under this path; `/shop-approvals/` does not exist |
| Approve application | `POST /api/super-admin/shop-applications/{application_id}/approve/` | Implemented; PATCH does not exist |
| Reject application | `POST /api/super-admin/shop-applications/{application_id}/reject/` | Implemented; PATCH does not exist |
| Shops list | `GET /api/super-admin/shops/` | Implemented |
| Shop detail | `GET /api/super-admin/shops/{shop_id}/` | Implemented |
| Activate/suspend shop | `PATCH /api/super-admin/shops/{shop_id}/status/` | Implemented |
| Review approved-shop license renewal | `POST /api/super-admin/shops/{shop_id}/license-documents/{document_id}/review/` | Implemented; verify or reject a pending renewal document |
| High-value order list | `GET /api/super-admin/high-value-orders/` | Implemented |
| High-value order detail | `GET /api/super-admin/high-value-orders/{order_id}/` | Implemented |
| Approve/reject high-value order | `POST /api/super-admin/orders/{order_id}/approval/` | Implemented; canonical decision route |
| Restricted existing-order review queue | `GET /api/super-admin/orders/restricted-shop/` | Implemented; suspended/expired-shop active orders |
| Authorize an existing order to continue | `POST /api/super-admin/orders/{order_id}/shop-review/` | Implemented; immutable audit log, no order status change |
| Customers list | `GET /api/super-admin/customers/` | Implemented |
| Payments list | `GET /api/super-admin/payments/` | Existing `Order` summary; separate payment ledger exists, but this list exposes no gateway transaction reference |
| Platform settings | `GET/PATCH /api/super-admin/settings/` | Fixed high-value threshold is read-only; nearby-radius updates are audited |
| Customer verification review | `GET/POST /api/super-admin/verification-sessions/` | Implemented for manual review; age-proof review requires confirmed DOB |
| Customer verification document | `GET /api/super-admin/verification-sessions/{session_id}/document/` | Super Admin-only private document download |
| Support tickets | `GET /api/super-admin/support-tickets/`, `PATCH /api/super-admin/support-tickets/{ticket_id}/` | Implemented |
| Legal policy CMS | `GET/POST /api/super-admin/legal-policies/`, `PATCH /api/super-admin/legal-policies/{policy_id}/` | Implemented |
| Reports | `GET /api/super-admin/reports/` | Implemented |
| Audit logs | `GET /api/super-admin/audit-logs/` | Implemented |

The separate `GET /api/super-admin/orders/pending-approval/` route is also implemented. It returns pending high-value orders without pagination; its response is described in the High-value orders section.

## 1. Admin identity

### `GET /api/super-admin/me/`

- **Request body/query:** None.
- **Success `200`:**

```json
{
  "id": 12,
  "email": "admin@example.com",
  "user_type": "super_admin",
  "is_active": true
}
```

`firebase_uid` is not returned.

## 2. Dashboard

### `GET /api/super-admin/dashboard/`

- **Request body/query:** None.
- **Success `200`:**

```json
{
  "active_shops": 4,
  "pending_shop_applications": 2,
  "suspended_shops": 1,
  "today_orders": 8,
  "today_gmv": "12345.00",
  "high_value_orders_pending": 1,
  "unread_notifications": 3
}
```

`today_gmv` is a decimal string. Today is calculated in the `Asia/Kolkata` timezone. The response does not include `pending_refunds`.

## Shop license renewal review

Approved or expired shops can submit a new license document through
`POST /api/shop-admin/application/documents/`. It remains pending and does not
change shop approval, activation, or customer visibility. A Super Admin reviews
the document through `GET /api/super-admin/shop-applications/{shop_id}/` (the
document metadata and download link are included) and posts:

```http
POST /api/super-admin/shops/{shop_id}/license-documents/{document_id}/review/
Authorization: Bearer <Django access JWT>
Content-Type: application/json
```

Verify a valid, unexpired document:

```json
{"decision": "verify"}
```

Reject a document with a reason:

```json
{"decision": "reject", "reason": "The submitted license is not valid."}
```

Verification is audited. It does not activate a disabled shop or unsuspend a
suspended shop; those existing Super Admin controls remain separate. Initial
pending shop applications continue to use the existing application review
endpoints, which now require a pending license document before approval.

## 2a. Super Admin notifications

Notifications are the existing `customers.Notification` records scoped to the authenticated Super Admin (`user=request.user`). The dashboard's `unread_notifications` count uses the same scope and unread flag.

### `GET /api/super-admin/notifications/`

- **Query:** `page` (default `1`), `page_size` (default `20`, range `1–100`).
- **Body:** None.
- **Success `200`:** Paginated envelope. Each result has:

```json
{
  "id": 14,
  "notification_type": "general",
  "type": "general",
  "title": "Review required",
  "message": "An order requires review.",
  "resource_type": "order",
  "resource_id": 31,
  "is_read": false,
  "created_at": "2026-10-23T06:00:00Z"
}
```

`type` is retained for backward compatibility; `notification_type` is the canonical field. Resource fields are `order`/order ID when the actual notification has an order, otherwise `null`.

### `POST /api/super-admin/notifications/{notification_id}/read/`

- **Body:** None.
- **Success `200`:** `{"id":14,"is_read":true}`. Marking an already-read notification remains successful.
- **404:** `{"detail":"Not found."}` when the notification does not exist or belongs to a different user.
- Shared authentication/permission and pagination errors are documented above.

## 3. Shop applications

The actual list path is `/shop-applications/`, not `/shop-approvals/`.

### `GET /api/super-admin/shop-applications/`

- **Query:** `status` (default `pending`; allowed `pending`, `verified`, `rejected`, `expired`, `all`), `search`, `page`, `page_size`.
- **Success `200`:**

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 9,
      "shop_name": "Sample Shop",
      "owner_name": "Owner Name",
      "owner_email": "owner@example.com",
      "phone": "9876543210",
      "city": "Pune",
      "state": "Maharashtra",
      "license_number": "LIC-123",
      "license_status": "pending",
      "application_status": "pending",
      "submitted_at": "2026-10-23T06:00:00Z",
      "documents": [
        {
          "id": 6,
          "license_number": "LIC-123",
          "expiry_date": "2030-12-31",
          "verification_status": "pending",
          "verified_at": null,
          "created_at": "2026-10-23T06:00:00Z",
          "download_url": "http://127.0.0.1:8000/api/shop-admin/application/documents/6/download/"
        }
      ]
    }
  ]
}
```

`documents` can be empty. A document download requires a Django JWT and is allowed only for a Super Admin or the owner Shop Admin.

### `GET /api/super-admin/shop-applications/{application_id}/`

- **Success `200`:** Returns the application fields above, plus:

```json
{
  "status_history": [
    {
      "id": 3,
      "actor_id": 12,
      "actor_email": "admin@example.com",
      "action": "shop_application_approved",
      "summary": "Approved Sample Shop.",
      "created_at": "2026-10-23T06:00:00Z"
    }
  ],
  "license_details": {
    "license_number": "LIC-123",
    "license_status": "pending"
  }
}
```

### `POST /api/super-admin/shop-applications/{application_id}/approve/`

- **Body:** `{}` or optional note:

```json
{"note":"License checked."}
```

- **Success `200`:**

```json
{
  "id": 9,
  "application_status": "verified",
  "license_status": "verified",
  "is_active": true
}
```

Approval activates the shop and marks its pending license documents verified. The latest pending document must not be expired. If it is expired, the endpoint returns `400`, for example:

```json
{"documents":"The latest license document is expired and cannot be approved."}
```

### `POST /api/super-admin/shop-applications/{application_id}/reject/`

- **Body:** `reason` required; `note` optional:

```json
{
  "reason": "License document is invalid.",
  "note": "Reviewed by admin."
}
```

- **Success `200`:**

```json
{
  "id": 9,
  "application_status": "rejected",
  "license_status": "rejected",
  "is_active": false
}
```

Pending license documents are marked rejected. Both decision endpoints accept only pending applications; a duplicate decision returns `400`, including `current_status`.

### `POST /api/super-admin/shop-applications/{application_id}/request-changes/`

- **Body:** `reason` required; `note` optional:

```json
{
  "reason": "Missing or unclear license document.",
  "note": "Optional reviewer note."
}
```

- **Success `200`:** `{"id":9,"application_status":"rejected"}`. The existing `rejected` status is used as the correction-required state so the Shop Admin can resubmit. Pending documents are marked rejected (not deleted), and the reason and note are added to immutable audit history.
- **400:** Invalid JSON shape, missing/blank reason, unknown fields, or application not pending.
- **401/403/404:** Shared authentication/permission/not-found responses above.

The application is removed from the pending review queue until the owner
resubmits. The Super Admin detail response includes audit metadata in
`status_history`, including the correction/rejection reason and note.

### Related Shop Admin application submission

The application lifecycle also has a Shop Admin API:

- `POST /api/shop-admin/onboarding/` is the first-time Shop Admin registration endpoint. Authenticate using a Django access JWT in the Authorization header; the token is not sent in the form body. The backend creates or links the corresponding Django user. A customer account is promoted to `shop_admin` only as part of a valid, successful application submission. Other roles cannot use this endpoint.
- Use `multipart/form-data` with the application fields below. The endpoint does not require an existing Shop Admin profile or shop.
- Success is `201`; response uses the application shape below, with `application_status: "pending"` and `is_active: false`. The current schema represents a pending application as a `shops.Shop` row with `license_status="pending"` and a separate license-document row; it does not activate the shop before Super Admin approval.
- The submission is recorded in the existing audit log as `shop_application_submitted` (or `shop_application_resubmitted`). Duplicate pending/verified applications return `400`; invalid/missing Django JWT credentials return `401`, and incompatible roles return `403`.
- `GET /api/shop-admin/application/` returns the current user's application.
- `POST /api/shop-admin/application/` submits a new application or resubmits an application whose existing status is `rejected` (including one returned for corrections). The endpoint is scoped to the authenticated Shop Admin's own application.
- Content type is `multipart/form-data`, not JSON. Fields: `shop_name`, `phone`, optional `email`, `address_line1`, optional `address_line2`, `city`, `state`, `postal_code`, optional `latitude`, optional `longitude`, `license_number`, `license_document`, `expiry_date`.
- `license_document` must be PDF/JPG/PNG and at most 10 MB; `expiry_date` must be in the future.
- It returns `201` with application fields, the replacement `documents` entry, and additive `review_history` entries containing prior review reasons and notes. All required onboarding fields, a supported license document, and a future expiry date are validated before the resubmission is accepted.
- A non-rejected existing application cannot be submitted again (`400`). A valid resubmission returns the application to `pending`; the Super Admin pending queue then includes it for re-review. Superseded documents are retained and marked rejected.
- A successful Super Admin approval makes the shop active and lets the Shop Admin manage products.

Onboarding success response example (timestamps and document ID are illustrative):

```json
{
  "id": 9,
  "shop_name": "Sample Shop",
  "phone": "9876543210",
  "email": "shop@example.com",
  "address_line1": "1 Market Road",
  "address_line2": "",
  "city": "Pune",
  "state": "Maharashtra",
  "postal_code": "411001",
  "license_status": "pending",
  "application_status": "pending",
  "is_active": false,
  "documents": [
    {
      "id": 6,
      "license_number": "LIC-123",
      "expiry_date": "2030-12-31",
      "verification_status": "pending",
      "verified_at": null,
      "created_at": "2026-10-23T06:00:00Z",
      "download_url": "https://api.example.com/api/shop-admin/application/documents/6/download/"
    }
  ],
  "submitted_at": "2026-10-23T06:00:00Z"
}
```

## 4. Shops

### `GET /api/super-admin/shops/`

- **Query:** optional `status` (`active`, `inactive`, `suspended`, `pending`, `verified`, `rejected`, `expired`, `all`), `license_status` (`pending`, `verified`, `rejected`, `expired`), `search`, `page`, `page_size`.
- **Success `200`:** Paginated envelope; each result:

```json
{
  "id": 9,
  "shop_name": "Sample Shop",
  "city": "Pune",
  "state": "Maharashtra",
  "is_active": true,
  "is_suspended_by_super_admin": false,
  "license_status": "verified",
  "owner": {
    "id": 12,
    "name": "Owner Name",
    "email": "owner@example.com"
  }
}
```

No `status` filter means no status filter is applied.

### `GET /api/super-admin/shops/{shop_id}/`

- **Success `200`:** Shop list fields above, plus:

```json
{
  "license_number": "LIC-123",
  "product_count": 14,
  "order_count": 25,
  "status_history": []
}
```

### `PATCH /api/super-admin/shops/{shop_id}/status/`

- **Body:** Both `is_active` boolean and non-empty `reason` required.

```json
{"is_active":false,"reason":"License expired."}
```

- **Success `200`:**

```json
{"id":9,"is_active":false}
```

Activating a shop requires `license_status == "verified"`; otherwise `400`. This endpoint changes activation/suspension status; it is not a shop-details editing endpoint.

## 5. High-value orders

Checkout assigns `pending_super_admin` to orders with `total_amount >= 15000.00`. This fixed threshold includes the ₹50 Platform fee (legacy field `application_fee`) in `total_amount`; the fee does not count toward the separate ₹1,000 minimum product subtotal. Such orders are kept out of Shop Admin order processing until Super Admin approval.

The pending queue also surfaces active legacy orders at or above the threshold
that have no Super Admin approval timestamp, regardless of their current
processing status. Shop Admin processing remains blocked for those orders until
approval. Approval does not rewrite an active legacy order's status; pending
orders move to `admin_approved`. No existing order rows are automatically
rewritten.

### `GET /api/super-admin/high-value-orders/`

- **Query:** `status` (default `pending`; accepts `all` or a valid Order status), `page`, `page_size`.
- **Success `200`:** Paginated envelope; each result:

```json
{
  "id": 31,
  "order_number": "31",
  "status": "pending_super_admin",
  "shop": {"id": 9, "shop_name": "Sample Shop"},
  "customer": {
    "id": 44,
    "email": "c***@example.com",
    "phone_masked": "******3210"
  },
  "subtotal": "14950.00",
  "application_fee": "50.00",
  "total_amount": "15000.00",
  "payment_status": "pending",
  "created_at": "2026-10-23T06:00:00Z"
}
```

Customer email and phone are masked.

### `GET /api/super-admin/orders/pending-approval/`

- **Query/body:** None. This additional route is not paginated.
- **Success `200`:**

```json
{
  "count": 1,
  "results": [
    {
      "id": 31,
      "order_number": "DRQ-0031",
      "status": "pending_super_admin",
      "shop_id": 9,
      "shop_name": "Sample Shop",
      "customer_name": "Customer Name",
      "subtotal": "14950.00",
      "application_fee": "50.00",
      "total_amount": "15000.00",
      "pickup_mode": "walk_in",
      "created_at": "2026-10-23T06:00:00Z"
    }
  ]
}
```

### `GET /api/super-admin/orders/restricted-shop/`

Returns paginated existing orders in `accepted`, `confirmed`, `preparing`, or
`ready` status whose shop is suspended, has an expired license, or is awaiting
approval of a renewal after its verified license expired. This is separate
from the ₹15,000 high-value order queue.

Each result includes the standard order summary plus `shop_restriction` and
`review_status` (`pending` or `authorized`). A pending renewal remains blocked
until the existing shop-application approval workflow verifies it.

### `POST /api/super-admin/orders/{order_id}/shop-review/`

Only an authenticated Super Admin can authorize an active existing order
affected by shop suspension or license expiry to continue:

```json
{"decision":"allow_continue","note":"Reviewed existing order."}
```

`note` is optional. Success returns `200` with `order_id`, the unchanged order
`status`, and `review_status: "authorized"`. The decision actor, timestamp,
and shop-state snapshot are stored in the immutable audit log. The
authorization applies only while that shop state remains unchanged; a later
suspension or expiry invalidates it. Reinstatement is governed by the existing
shop-application approval workflow. This endpoint does not cancel orders,
restore stock, or change the order status.
Pending/rejected/unapproved shops must use the existing Super Admin
shop-application workflow; this per-order action cannot authorize them.

### `GET /api/super-admin/high-value-orders/{order_id}/`

- **Success `200`:** High-value order list fields, plus:

```json
{
  "items": [
    {
      "product_name": "Sample Product",
      "quantity": 1,
      "unit_price": "14950.00",
      "subtotal": "14950.00"
    }
  ],
  "approval_history": [],
  "decision_state": "pending"
}
```

`decision_state` is computed as `pending`, `approved`, `rejected`, or `not_pending`.

### Canonical approve/reject endpoint: `POST /api/super-admin/orders/{order_id}/approval/`

Approve request (`note` optional):

```json
{"decision":"approved","note":"Compliance review passed."}
```

Approve success `200`:

```json
{"order_id":31,"status":"admin_approved","decision":"approved"}
```

Reject request (`reason` required; `note` optional):

```json
{"decision":"rejected","reason":"Compliance review failed.","note":"Reviewed."}
```

Reject success `200`:

```json
{"order_id":31,"status":"cancelled","decision":"rejected"}
```

Only active, unapproved high-value orders can be decided. Reject returns reserved product stock and creates notifications. Invalid decision, invalid body fields, or an order that is already approved or terminal returns `400`.

### Also-available legacy endpoints

- `POST /api/super-admin/high-value-orders/{order_id}/approve/`: body `{}` or `{"note":"..."}`; success is `{"order_id":31,"status":"admin_approved","decision":"approved"}`.
- `POST /api/super-admin/high-value-orders/{order_id}/reject/`: body `{"reason":"...","note":"..."}` (`reason` required); success is `{"order_id":31,"status":"cancelled","decision":"rejected"}`.

These aliases use the same decision logic. Prefer the canonical `/orders/{order_id}/approval/` endpoint for new frontend integration.

## 6. Customers

### `GET /api/super-admin/customers/`

- **Query:** `status` (`active`, `blocked`, `inactive`, `all`; default `all`), `search` (email/phone/username), `page`, `page_size`.
- **Success `200`:** Paginated envelope; each result:

```json
{
  "id": 44,
  "email": "c***@example.com",
  "phone_masked": "******3210",
  "is_active": true,
  "is_blocked": false,
  "age_verified": true,
  "order_count": 5,
  "created_at": "2026-10-20T10:00:00Z"
}
```

Email/phone are masked. `firebase_uid` is not returned.

### Additional customer routes

- `GET /api/super-admin/customers/{customer_id}/` returns the same customer summary.
- `PATCH /api/super-admin/customers/{customer_id}/status/` requires `{"is_blocked":true,"reason":"Policy violation."}` and returns the updated customer summary. Both fields are required.

## 7. Payments

### `GET /api/super-admin/payments/`

- **Auth:** Django access JWT for an active Django Super Admin.
- **Query:** `page`, `page_size` (default `20`, range `1–100`); optional `payment_status` (`pending`, `paid`, `failed`, `refunded`), `order_id`, `shop_id`, `from`, `to` (`YYYY-MM-DD`, India timezone), and `search` (shop name or numeric order ID).
- **Data source:** This endpoint continues to project `orders.Order`, even though the new source has a separate `OrderPayment` ledger. `id` is the order ID, `order_number` is the existing string form of the order ID, and `amount` is the order's `total_amount`. No gateway transaction-reference field is returned by this list. New Test Mode payment/refund contracts and activation blockers are in [Razorpay payments](./docs/razorpay-payments.md).
- **Success `200`:** Paginated envelope:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 31,
      "order_id": 31,
      "order_number": "31",
      "shop": {"id": 9, "shop_name": "Sample Shop"},
      "amount": "15050.00",
      "payment_status": "pending",
      "payment_method": "online",
      "created_at": "2026-10-23T06:00:00Z"
    }
  ]
}
```

- **400:** Invalid pagination/filter values, for example `{"payment_status":"Unsupported payment status."}`.
- **401/403/404:** Shared authentication/permission/not-found responses above. A missing order ID filter returns an empty result set, not 404.

## 7a. Platform settings

`GET /api/super-admin/settings/` returns the fixed high-value order threshold and the customer nearby-shop search radius:

```json
{"high_value_order_threshold":"15000.00","nearby_shop_radius_km":"10.00","updated_at":"2026-10-24T06:00:00Z"}
```

`PATCH /api/super-admin/settings/` accepts updates to the nearby-shop radius only, between `0.10` and `100.00` km. Attempts to change `high_value_order_threshold` return `400`; the threshold is fixed at `15000.00`:

```json
{"nearby_shop_radius_km":"25.00"}
```

The fixed high-value threshold is used by checkout, Shop Admin order handling,
and Super Admin high-value order queues. The nearby-shop radius controls
`GET /api/shop/nearby/` and is also returned as `radius_km` in that endpoint's
response. Nearby-radius changes are written to the immutable audit log.

## 7b. Customer workflow administration

- `GET /api/super-admin/verification-sessions/?status=pending` lists customer
  document-verification requests using the standard pagination envelope.
- The list response includes `document_download_url`; only a Super Admin can
  access `GET /api/super-admin/verification-sessions/{session_id}/document/`.
- `POST /api/super-admin/verification-sessions/` accepts
  `{"session_id":12,"status":"approved"}` or a rejection with a non-empty
  `reviewer_note`. Review is manual; no document-verification provider or
  callback has been integrated. Document files are not embedded in responses.
- Age-proof sessions use `document_type: "age_proof"`. Approval requires
  `{"session_id":12,"status":"approved","confirmed_date_of_birth":"2000-01-01"}`
  with `confirmed_date_of_birth` in strict `YYYY-MM-DD` form; future dates
  are rejected. The confirmed DOB is saved on the customer, returned with the
  review response, and recorded in the audit log. Only approved proof
  confirming age 21 or older sets `age_verified: true`; approving proof for a
  younger customer records the DOB but leaves age verification false.
- `GET /api/super-admin/support-tickets/?status=open` lists tickets.
- `PATCH /api/super-admin/support-tickets/{ticket_id}/` accepts
  `{"status":"resolved","admin_response":"..."}`.
- `GET/POST /api/super-admin/legal-policies/` lists policies or creates one
  using `slug`, `title`, `version`, `content`, and optional `is_published`.
- `PATCH /api/super-admin/legal-policies/{policy_id}/` updates
  `title`, `version`, `content`, or `is_published`.

Legal policy content is supplied and maintained by administrators; no legal
copy is pre-populated by the backend.

## 8. Reports

### `GET /api/super-admin/reports/`

- **Query:** `range=7d`, `30d`, or `90d`; default `7d`.
- **Success `200`:**

```json
{
  "range": "7d",
  "orders_count": 12,
  "gmv": "45000.00",
  "active_shops": 4,
  "new_shops": 1,
  "new_customers": 7,
  "order_trend": [
    {"date":"2026-10-23","orders":2,"gmv":"8000.00"}
  ],
  "top_shops": [
    {"shop_id":9,"shop_name":"Sample Shop","orders":5,"gmv":"20000.00"}
  ],
  "category_trends": []
}
```

`category_trends` is currently always empty. If there are no orders in the range, `gmv` is `"0.00"` and trend/shop arrays are empty. Unsupported range returns `400`.

## 9. Audit logs

### `GET /api/super-admin/audit-logs/`

- **Query:** optional `actor_id` (integer), `action`, `resource_type`, `from`, `to` (dates in `YYYY-MM-DD`); `page`, `page_size`.
- **Success `200`:** Paginated envelope; each result:

```json
{
  "id": 3,
  "actor_id": 12,
  "actor_email": "admin@example.com",
  "action": "shop_suspended",
  "resource_type": "shop",
  "resource_id": 9,
  "summary": "Suspended Sample Shop.",
  "created_at": "2026-10-23T06:00:00Z"
}
```

`from`/`to` are interpreted as India-local day boundaries. Invalid date, integer, or a `from` date later than `to` returns `400`.

## Relevant source files and code organization

### URL configuration

`config/urls.py` mounts `shop_admin.super_admin_urls` at `/api/super-admin/`. The complete route declarations live in `shop_admin/super_admin_urls.py`; route names and view bindings include:

```python
path("me/", views.super_admin_me, name="super-admin-me"),
path("dashboard/", views.super_admin_dashboard, name="super-admin-dashboard"),
path("shop-applications/", views.super_admin_shop_applications,
     name="super-admin-shop-applications"),
path("shop-applications/<int:application_id>/approve/",
     views.super_admin_approve_shop_application,
     name="super-admin-shop-application-approve"),
path("shop-applications/<int:application_id>/reject/",
     views.super_admin_reject_shop_application,
     name="super-admin-shop-application-reject"),
path("shops/", views.super_admin_shops, name="super-admin-shops"),
path("shops/<int:shop_id>/", views.super_admin_shop_detail,
     name="super-admin-shop-detail"),
path("shops/<int:shop_id>/status/", views.super_admin_shop_status,
     name="super-admin-shop-status"),
path("customers/", views.super_admin_customers, name="super-admin-customers"),
path("orders/pending-approval/", views.super_admin_pending_orders,
     name="super-admin-pending-orders"),
path("orders/<int:order_id>/approval/", views.super_admin_order_approval,
     name="super-admin-order-approval"),
path("high-value-orders/", views.super_admin_high_value_orders,
     name="super-admin-high-value-orders"),
path("high-value-orders/<int:order_id>/approve/",
     views.super_admin_approve_high_value_order,
     name="super-admin-high-value-order-approve"),
path("high-value-orders/<int:order_id>/reject/",
     views.super_admin_reject_high_value_order,
     name="super-admin-high-value-order-reject"),
path("reports/", views.super_admin_reports, name="super-admin-reports"),
path("audit-logs/", views.super_admin_audit_logs,
     name="super-admin-audit-logs"),
```

The URL module contains additional detail, request-changes, notification, and customer-status routes; see `shop_admin/super_admin_urls.py` for the complete list.

### Views and serializers

- Super Admin view implementations are in `shop_admin/super_admin_views.py`; there is no separate Super Admin `api_views.py`.
- Most Super Admin response dictionaries are built by `_application_summary`, `_shop_summary`, `_customer_summary`, `_order_summary`, and endpoint view functions. They are not produced by dedicated DRF Super Admin serializers.
- Shared pagination and query validation are implemented in `_paginated_response`, `_validate_payload`, and related helpers in `shop_admin/super_admin_views.py`.
- Shop application submission fields are validated by `ShopApplicationSubmissionSerializer` in `shops/serializers.py`.
- Uploaded license-document response fields are serialized by `ShopLicenseDocumentSerializer` in `shops/serializers.py`.
- The file download and Shop Admin application endpoints are routed from `shop_admin/urls.py` to `shop_admin/views.py`.
- `shop_admin/serializers.py` contains legacy Shop Admin serializers, not the Super Admin response serializers.
