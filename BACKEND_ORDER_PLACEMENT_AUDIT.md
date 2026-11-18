# DRINQO Backend — Order Placement Audit

Audit date: 17 November 2026 (Asia/Calcutta). Project: `D:\drinqo_backend`.

> Historical audit snapshot. Customer/staff handover OTP was subsequently
> retired; the current implementation uses staff-confirmed handover.

## Scope, evidence and verdict

Read-only source audit of the active applications referenced by `config/urls.py`, including customer cart, checkout, orders, Shop Admin, Super Admin, payment ledger and handover OTP. The nested `drinqo_shop_admin_patch` copies are not the active URL configuration. Only this requested report was created; no application code, configuration, migrations or database records were intentionally changed. No application HTTP requests, payment-provider calls, tests, migrations or data-fixing commands were executed. Some GET endpoints themselves write data, so they were inspected rather than called.

“Verified” below means verified against source, not a successful live checkout. JSON examples use synthetic IDs, names, coordinates and timestamps to show the exact response structure; these are not captured database responses. Dynamic values and validation messages are explicitly identified. File references use current source line numbers.

**Verdict:** the main placement implementation uses saved customer details, authoritative prices, atomic order/item/stock changes and ownership filters. High-value approval is enforced at **total_amount >= ₹15,000**, including the ₹50 fee. However, the requested `POST /api/orders/` does not exist; the implemented endpoint is `POST /api/orders/create/`. Fulfillment cannot complete with the current disabled payment gateway. Inventory writers outside checkout can lose stock updates. Payment eligibility and legacy rejection contracts have confirmed inconsistencies. Do not treat passing Django checks as end-to-end or concurrency certification.

## 1. End-to-end order workflow

1. Authenticate with a Django JWT for a `customer` user. Blocked customers are rejected by the custom JWT authenticator.
2. Persist Personal Details through `/api/customer/profile/`; persist user DOB and a 21+ declaration through `/api/accounts/age-verification/`. Save a customer-owned vehicle.
3. Add active products from one eligible shop to the persistent customer cart. A cart is not an inventory reservation.
4. POST checkout preview with vehicle ID and current coordinates. The server checks customer eligibility, vehicle ownership, shop eligibility, product activity, stock, current prices, minimum subtotal and distance.
5. POST the same checkout payload to `/api/orders/create/`. The server locks the cart, cart items, shop and products; recomputes prices; creates the order and item snapshots; immediately deducts stock; clears cart items; creates notifications, all in one atomic transaction.
6. Initial order state is `pending`, or `pending_super_admin` when subtotal + ₹50 >= ₹15,000. Payment is initially `pending` and method is always `online`.
7. For a high-value order, Super Admin approves to `admin_approved`, or rejects to `cancelled` and restores stock. Unapproved orders cannot be processed by Shop Admin.
8. Shop Admin accepts `pending`/`admin_approved` to `confirmed`. Acceptance does not require payment.
9. Customer initiates payment after shop confirmation. Only a server-verified captured payment ledger permits `confirmed -> preparing -> ready`.
10. Customer issues a handover OTP for a ready, paid order. Owning Shop Admin submits the challenge ID and OTP to `/api/orders/{id}/verify-pickup/`; success atomically consumes the OTP, sets pickup verification and completes the order.
11. Customer polls history/detail/tracking/payment status and reads persisted notifications. There is no implemented order WebSocket/SSE or order push-delivery pipeline in the inspected active code.

Sources: `customers/views.py:224,263,443,513`; `orders/views.py:68,109,247,254,326,353`; `shop_admin/api_views.py:109,686,1013`; `orders/payments.py:125,179`; `orders/handover_otp.py:170,198`.

## 2. Verified endpoint contract table

All paths have trailing slashes. `C` = JWT customer; `S` = JWT owning Shop Admin; `A` = JWT Super Admin. All protected paths reject missing/invalid authentication. Customer object queries are scoped to the current customer; Shop Admin object queries are scoped to its assigned shop. `Order` means the exact serializer shape in section 5. Standard errors are described in section 6. HEAD/OPTIONS may be supplied by DRF except where explicitly disabled; business methods follow.

### Customer, cart and checkout

| Method | URL | Role | Request | Success | Endpoint errors / source |
|---|---|---|---|---|---|
| GET, PUT, PATCH | `/api/customer/profile/` | C | Profile fields for write | 200 profile | Field errors 400; `customers/views.py:74`, serializers:29 |
| POST | `/api/accounts/age-verification/` | C | `date_of_birth` and/or `age_self_declaration:true` | 200 age fields | 400 DOB/declaration errors; `accounts/views.py:553` |
| GET, POST | `/api/customer/vehicles/` | C | POST `vehicle_type`, `vehicle_number`; optional `vehicle_name` | GET paginated vehicles; POST 201 vehicle | 400 validation; `customers/views.py:136` |
| GET, PUT, PATCH, DELETE | `/api/customer/vehicles/{id}/` | C | Vehicle fields for write | 200 vehicle / DELETE 204 empty | 400/404; `customers/views.py:164` |
| GET | `/api/customer/cart/` | C | None | 200 Cart | GET creates profile/cart if absent; views:224 |
| POST | `/api/customer/cart/items/` | C | `product_id`, optional `quantity` default 1 | 201 new item / 200 existing item incremented | 400 stock/mixed shop/validation; 404 unavailable; views:263 |
| PATCH | `/api/customer/cart/items/{id}/` | C | `quantity` | 200 CartItem | 400 inactive/shop/stock/validation; 404 not owned; views:443 |
| DELETE | `/api/customer/cart/items/{id}/delete/` | C | None | 204 empty | 404 not owned; views:502 |
| POST | `/api/customer/checkout/preview/` | C | CheckoutPayload, section 4 | 200 Preview, including `can_checkout:false` | 400/403 detailed below; views:513 |
| POST | `/api/orders/create/` | C | CheckoutPayload | 201 Order | 400/403 detailed below; `orders/views.py:68` |
| POST | `/api/orders/` | — | — | **No route** | Django resolver 404; do not assume a JSON error envelope |
| GET | `/api/orders/history/` | C | `?page=N` | 200 paginated Order | 404 invalid page; orders/views:326 |
| GET | `/api/orders/{id}/` | C | None | 200 Order | 404 not owned; views:340 |
| POST | `/api/orders/{id}/cancel/` | C | No fields consumed | 200 Order (`cancelled`) | 409 forbidden state, 404; views:353 |
| GET | `/api/orders/{id}/tracking/` | C | None | 200 tracking object | 404; views:696 |
| GET | `/api/orders/{id}/payment-status/` | C | None | 200 status + projection | 404; views:418 |
| GET | `/api/customer/notifications/` | C | `?page=N` | 200 paginated notifications | Ownership filtered; customers/views:752 |
| POST | `/api/customer/notifications/{id}/read/` | C | None | 200 full Notification object with `is_read:true` | 404; customers/views:762 |

Cart quantity fields reject booleans/floats and require integers >=1 (integer strings accepted). A zero quantity does not delete an item. Unknown fields on the cart and checkout serializers are ignored, not used to override prices. `vehicle_id` uses DRF IntegerField rather than the stricter cart integer class. Notification objects have exactly `id,order,notification_type,title,message,is_read,created_at`; `order` is the linked order ID or null. Notification read returns the same full object, not an acknowledgement-only wrapper.

### Shop Admin

| Method | URL | Role / constraints | Body | Success / failure contract |
|---|---|---|---|---|
| GET | `/api/shop-admin/orders/` | S | None | 200 bare Order array; excludes unapproved active high-value orders; no pagination/filter support; restricted shop can receive 403 |
| GET | `/api/shop-admin/orders/{id}/` | S | None | 200 Order; 404 missing/foreign/unapproved high-value; restricted shop 403 |
| POST | `/api/shop-admin/orders/{id}/decision/` | S, approved active licensed shop | `decision:"accepted"` OR `decision:"rejected",reason_code` | 200 Decision object; 400 bad decision/reason; 409 state/high-value/foreign-item conflict |
| POST | `/api/shop-admin/orders/{id}/accept/` | S, approved active licensed shop | No fields consumed | 200 Accept object; 403 high-value; 400 state; 409 foreign item |
| POST | `/api/shop-admin/orders/{id}/reject/` | S, approved active licensed shop | No fields consumed | 200 LegacyReject object; 403 high-value; 400 unless `pending`; changes state to `cancelled` |
| POST | `/api/shop-admin/orders/{id}/status/` | S, operational shop or approved existing-order exception | `status:"preparing"` or `"ready"` | 200 StatusAction; 400 invalid state/value; 403 restriction/approval; 402 payment required |
| POST | `/api/shop-admin/orders/{id}/pending/` | S, approved active licensed shop | None | 200 PendingAction for `confirmed`, no mutation; 400 other state; 403 high-value |
| POST | `/api/shop-admin/orders/{id}/complete/` | S, approved active licensed shop | None | **Always errors**: 400 requires pickup verification (or 403 high-value); use verify-pickup |
| GET | `/api/orders/{id}/live-location/` | C or S | None | 200 Location; other roles 403; inaccessible order 404 |
| POST | `/api/orders/{id}/live-location/` | S | `is_sharing:true,latitude,longitude` OR `is_sharing:false` | 200 Location; 400 shape/state; 403 approval/shop/role; 404 ownership |
| POST | `/api/orders/{id}/verify-pickup/` | S | `challenge_id`, `otp` string of six ASCII digits | 200 HandoverSuccess; 400 shape; 402 unpaid; 403 approval/restriction; 404 ownership; 409 challenge/state; 422 incorrect OTP; 429 throttle |

Sources: `shop_admin/api_views.py:597-1122`, `orders/views.py:445-693`, `orders/handover_views.py:123`, `orders/handover_otp.py:198`.

### Super Admin approval and payments

| Method | URL | Role | Request / query | Success |
|---|---|---|---|---|
| GET | `/api/super-admin/orders/pending-approval/` | A | None | 200 `{"count":n,"results":[PendingApproval]}`; unpaginated |
| GET | `/api/super-admin/high-value-orders/` | A | `status` default `pending`, `all` or actual order state; `page`, `page_size` | 200 paginated HighValueSummary |
| GET | `/api/super-admin/high-value-orders/{id}/` | A | None | 200 summary + items + approval_history + decision_state |
| POST | `/api/super-admin/high-value-orders/{id}/approve/` | A | `{}`; optional `note`, `decision:"approved"` | 200 `{"order_id":id,"status":"admin_approved","decision":"approved"}` for new pending order |
| POST | `/api/super-admin/high-value-orders/{id}/reject/` | A | Required nonempty `reason`; optional `note`, `decision:"rejected"` | 200 `{"order_id":id,"status":"cancelled","decision":"rejected"}` |
| POST | `/api/super-admin/orders/{id}/approval/` | A | Required `decision:"approved"` or `"rejected"`; rejection requires `reason`; optional `note` | Same decision response |
| GET | `/api/super-admin/orders/restricted-shop/` | A | `page`, `page_size` | 200 paginated restricted-order summaries |
| POST | `/api/super-admin/orders/{id}/shop-review/` | A | `decision:"allow_continue"`, optional `note` | 200 `{"order_id":id,"status":current,"review_status":"authorized"}` |
| POST | `/api/orders/{id}/payment/initiate/` | C | Exactly `{}` | 201 new / 200 reused PaymentPublic with checkout; races may return 409 PaymentPublic without checkout |
| POST | `/api/orders/{id}/payment/verify/` | C | `razorpay_order_id`, `razorpay_payment_id`, `razorpay_signature` | 200 PaymentPublic |
| POST | `/api/orders/{id}/payment/reconcile/` | C | Exactly `{}` | 200 PaymentPublic |
| POST | `/api/orders/payments/razorpay/webhook/` | Provider HMAC, no JWT | Raw provider JSON; `X-Razorpay-Signature`, `X-Razorpay-Event-Id` | 200 `{"received":true}` |
| GET | `/api/orders/{id}/handover-otp/` | C | None | 200 Challenge (does not issue a new code) |
| POST | `/api/orders/{id}/handover-otp/` | C | Exactly `{}` | 201 issued / 200 active challenge reused |
| POST | `/api/orders/{id}/handover-otp/reissue/` | C | Exactly `{"challenge_id":"UUID"}` | 201 replacement Challenge |

Approval failures: 400 invalid payload/already decided/inactive state, 404 nonexistent or below-threshold order, 403 wrong role. Strict Super Admin payload validation rejects unknown fields. All payment/handover bodies are JSON objects with strict unknown-field rejection. Payment endpoints are throttled 15/min per user. Handover read/issue/verify rates are 60/10/30 per minute; issuance also has durable per-order limits.

Sources: `orders/urls.py`, `customers/urls.py`, `shop_admin/urls.py:156`, `shop_admin/super_admin_urls.py:115`, `shop_admin/super_admin_views.py:1252-1558`, `orders/payment_views.py`, `orders/handover_views.py`.

## 3. Customer cart and saved Personal Details

`Cart.customer` is OneToOne to CustomerProfile; CartItem has a unique `(cart, product)` constraint. Storage is database-persistent across sessions/devices; successful checkout empties items but retains the cart. Prices are read from Product, never stored/trusted from cart input. Add checks product activity, shop availability and stock, then locks the parent cart and existing item. It rejects another shop rather than automatically clearing the existing cart. There is no checkout `shop_id` input: the shop is derived from cart products and all items must match it.

PATCH locks cart/item/product, checks ownership, activity, availability and stock. DELETE filters by ownership but does not lock the parent cart or coordinate with checkout. Add stock reads are outside its cart transaction and do not reserve stock; later preview/create revalidation remains necessary. GET cart can display a stale, unavailable or overstocked item; it is not a checkout eligibility response. Cart updates do not reliably advance the parent Cart.updated_at.

Exact CartItem example (201 add, 200 increment/PATCH):

```json
{"id":1,"product_id":10,"product_name":"Example product","product_price":"500.00","shop_id":2,"shop_name":"Example shop","quantity":1,"subtotal":500.0,"created_at":"2026-11-17T06:00:00Z","updated_at":"2026-11-17T06:00:00Z"}
```

Cart shape: `{"id":1,"items":[CartItem],"total_items":1,"total_amount":500.0,"created_at":"ISO timestamp","updated_at":"ISO timestamp"}`. This shape notation is not literal JSON: `CartItem` expands to the object above. `product_price` is a decimal string; cart subtotal/total are method-field Decimal values rendered as JSON numbers, and an empty total is integer `0`. Total is products only, with no ₹50 fee.

Checkout eligibility uses persisted fields, not repeated Profile Setup input and not solely `profile_completed_at`:

| Requirement | Exact implementation |
|---|---|
| Personal Details | Existing CustomerProfile; nonblank `full_name`, `address_line1`, `city`, `state`, `postal_code`, and `profile.phone or user.phone` |
| Phone | Presence only at checkout; no VerifiedPhoneIdentity requirement, OTP proof or format validation here |
| DOB | Authoritative `User.date_of_birth` must be present and age >=21 today |
| Declaration | `User.age_self_declared` AND timestamp, or documentary `is_age_verified(user)` |
| Documentary fallback | age_verified flag plus approved reviewed age-proof session and matching Super Admin audit for the current DOB |
| Vehicle | Existing saved vehicle belonging to this CustomerProfile; no uploaded vehicle documents checked |

**Confirmed from source:** valid saved details and persisted declaration permit preview/create without resubmitting name/address/phone/DOB or calling Profile Setup again. `customers/tests.py:604` contains an existing test specifically for preview and order creation with persisted self-declaration and no documentary proof; it was inspected, not run.

Personal Details and DOB are split across CustomerProfile and User. Updating customer profile DOB synchronizes User and resets age eligibility when changed, but submitting a profile update that omits DOB can also reset a User DOB that differs from profile DOB: `customers/views.py:101-122` derives requested DOB from the profile fallback. The age-verification API updates User DOB but not profile DOB (`accounts/views.py:553-612`). Clients must not assume these two DOB values are always synchronized. See finding F4.

Onboarding API example: `POST /api/accounts/age-verification/` with `{"date_of_birth":"1990-01-01","age_self_declaration":true}`. Success fields are exactly `date_of_birth`, `age_verified`, `age_verified_at`, `age_self_declared`, `age_self_declared_at`, `age_verification_status`; documentary status can still be `proof_required` while checkout is allowed by self-declaration. CustomerProfile response fields are `id,full_name,date_of_birth,phone,phone_verified,verified_phone_last4,email,address_line1,address_line2,city,state,postal_code,profile_completed_at,created_at,updated_at`.

Sources: `customers/models.py:8,68,136,155`; `customers/serializers.py:29,73,120,181,224`; `customers/checkout_eligibility.py:27`; `customers/age_verification.py:10,50,59`.

## 4. Exact checkout request and preview

CheckoutPayload for both preview and creation:

```json
{"pickup_mode":"vehicle","vehicle_id":3,"latitude":18.520430,"longitude":73.856744}
```

`pickup_mode` may be omitted and defaults to `vehicle`; its only accepted value is `vehicle`. `walk_in` exists in the Order model but is rejected for new checkout. `vehicle_id` is required through object validation, integer >=1 and owned by current customer. Latitude and longitude are required, finite decimal numbers/numeric strings with at most six fractional digits and geographic ranges [-90,90] and [-180,180]. Booleans, NaN and infinity are rejected. There is no address, vehicle registration, customer ID, product list, shop ID, price, total, payment method, payment status or idempotency key in this contract.

No arbitrary delivery address or handoff point is saved on the order. Coordinates are used for an immediate Haversine distance check against shop coordinates; they are not persisted as the selected handoff location. Distance <=100 metres passes. New checkout is vehicle-only. The eventual OTP handover does not recalculate distance or require fresh location evidence.

An eligible shop must be open, active, unsuspended, have `license_status:"verified"`, and have a latest verified license document whose expiry is strictly after the local date. A license expiring today is considered expired. Opening-hours text is not evaluated here; `is_open` is the switch. Missing shop coordinates block checkout.

Exact preview success structure, synthetic cart containing one ₹500 product:

```json
{
  "can_checkout":true,
  "shop_id":2,
  "shop_name":"Example shop",
  "items":[{"product_id":10,"product_name":"Example product","quantity":1,"unit_price":"500.00","subtotal":"500.00"}],
  "total_items":1,
  "subtotal":"500.00",
  "minimum_order_amount":"500.00",
  "distance_to_shop_m":0.0,
  "pickup_radius_m":100,
  "application_fee":"50.00",
  "platform_fee":"50.00",
  "delivery_fee":"0.00",
  "total_amount":"550.00",
  "pickup_mode":"vehicle",
  "vehicle":{"id":3,"vehicle_type":"car","vehicle_number":"EXAMPLE123","vehicle_name":"Example car"}
}
```

`platform_fee` aliases `application_fee`; it is not a second charge. `detail` is absent on eligible preview. Preview returns **HTTP 200**, the same shape with `can_checkout:false` and one of these exact detail strings for business blockers:

| Condition | `detail` | Other values |
|---|---|---|
| subtotal <500 | `Minimum product subtotal is ₹500 to place an order.` | Current product totals remain in response |
| shop coordinates absent | `Shop location is unavailable; checkout cannot be validated.` | `distance_to_shop_m:null` |
| distance >100m | `You must be within 100 meters of the shop to check out.` | Actual distance retained |

When several apply, the minimum-subtotal message has precedence, then missing shop location, then distance. Preview does not reserve inventory or promise a price snapshot. Stock, shop eligibility or cart changes can invalidate a successful preview before creation.

## 5. Exact order creation response and transaction behavior

POST `/api/orders/create/`, CheckoutPayload only. Successful HTTP **201** response is a bare Order object, with no outer `success`, `message`, `data`, `order_id` or payment checkout wrapper:

```json
{
  "id":41,
  "order_number":"DRQ-0041",
  "handover_challenge_id":null,
  "shop":2,
  "status":"pending",
  "payment_method":"online",
  "payment_status":"pending",
  "pickup_mode":"vehicle",
  "vehicle":3,
  "vehicle_details":{"id":3,"vehicle_type":"car","vehicle_number":"EXAMPLE123","vehicle_name":"Example car"},
  "subtotal":"500.00",
  "application_fee":"50.00",
  "platform_fee":"50.00",
  "total_amount":"550.00",
  "items":[{"id":71,"product":10,"product_name":"Example product","unit_price":"500.00","quantity":1,"subtotal":"500.00"}],
  "created_at":"2026-11-17T06:00:00Z",
  "updated_at":"2026-11-17T06:00:00Z"
}
```

For a high-value order only values change, including initial `status:"pending_super_admin"`. `id` is the authoritative identifier used for all subsequent URLs. `order_number` is a formatted display value with minimum four digits, not a separate identifier. Item IDs are OrderItem IDs. `shop`, `vehicle` and item `product` are integer foreign keys. Vehicle details may be null if the saved vehicle is later deleted. Vehicle fields are live related data, not immutable snapshots. Name/unit price/quantity/subtotal on OrderItem are snapshots.

Atomic block: locks persistent Cart row, locks only CartItem rows in joined cart query (`of=("self",)`), validates one shop, locks shop, locks fresh Product rows ordered by ID, rechecks activity and stock, recomputes item snapshots, verifies minimum and geofence, inserts Order/OrderItems, deducts stock, deletes cart items, inserts customer and applicable Super Admin notifications. Unhandled exceptions inside the block roll back all these writes. Validation returns occur before stock/order writes (an empty cart can still be created by get_or_create). There is no remote payment call inside creation.

Concurrent identical submissions normally serialize on the same Cart row: first creates and empties it, second receives 400 `Cart is empty.`. This limits ordinary double-click duplication but **is not idempotency**: a lost success response cannot be replayed to retrieve the same order; a cart repopulated between retries can create another order. No Idempotency-Key, unique checkout token, preview version or replay record exists. No application-specific lock-timeout/deadlock recovery response is implemented. Safety depends on PostgreSQL locks and all stock writers honoring them.

Sources: `orders/views.py:73-323`; `orders/serializers.py:9-103`; `orders/models.py:150`; `orders/locking.py:7`.

## 6. Exact checkout/creation errors and common API errors

Eligibility failures run before request-payload validation. Preview and create share these exact body forms:

| HTTP | Exact JSON body |
|---|---|
| 400 | `{"detail":"Complete your personal details before checkout.","code":"profile_incomplete","missing_fields":["full_name","phone","address_line1","city","state","postal_code"]}` |
| 403 | `{"detail":"Date of birth is required before checkout.","code":"date_of_birth_required"}` |
| 403 | `{"detail":"You must be at least 21 years old to place an order.","code":"underage_customer"}` |
| 403 | `{"detail":"Confirm that you are 21 years or older before checkout.","code":"age_self_declaration_required"}` |
| 400 | `{"detail":"Invalid checkout request.","errors":{"latitude":["This field is required."],"longitude":["This field is required."]}}` for `{}` with an otherwise eligible customer |
| 400 | `{"detail":"Invalid checkout request.","errors":{"vehicle_id":["Vehicle ID is required for vehicle pickup."]}}` when valid coordinates are supplied without vehicle ID |
| 400 | `{"detail":"Invalid checkout request.","errors":{"pickup_mode":["\"walk_in\" is not a valid choice."]}}` for unsupported walk-in with otherwise valid fields |
| 400 | `{"detail":"Vehicle not found."}` |
| 400 | `{"detail":"Cart is empty."}` |
| 400 | `{"detail":"Cart can contain products from only one shop."}` |
| 400 | `{"detail":"Product 'Example product' is no longer available."}` (product name dynamic) |
| 400 | `{"detail":"Shop 'Example shop' is currently unavailable."}` (shop name dynamic) |
| 400 | `{"detail":"Only 2 units of 'Example product' are available."}` (stock/name dynamic) |

`missing_fields` is only the actual missing subset, in the listed order; the full array above is used for an absent profile. Field-validation error objects include all field failures; object-level missing-vehicle validation runs only after field validation succeeds. Coordinate errors use arrays under the relevant field, e.g. `A valid number is required.`, `A finite number is required.`, `Ensure this value is greater than or equal to -90.`, `Ensure this value is less than or equal to 90.`, or DRF decimal precision errors. A null required field gives `This field may not be null.`. Non-object JSON gives DRF `non_field_errors` nested under `errors`; malformed JSON is a parser 400 before view business validation.

Creation-only blocking responses (preview instead returns the 200 blockers described above):

```json
{"detail":"Minimum product subtotal is ₹500 to place an order.","minimum_order_amount":500.0,"current_subtotal":450.0}
```

```json
{"detail":"Shop location is unavailable; checkout cannot be validated."}
```

```json
{"detail":"You must be within 100 meters of the shop to place an order.","pickup_radius_m":100}
```

All three are HTTP 400. Minimum/current Decimal values here are rendered as JSON numbers because this error bypasses a DecimalField serializer; `current_subtotal` is dynamic.

Cart errors: POST unavailable product/shop 404 `{"detail":"Product not found or unavailable."}`; stock 400 `{"detail":"Requested quantity exceeds available stock."}`; mixed-shop 400 `{"detail":"Your cart already contains products from another shop. Please clear your cart before shopping from this shop."}`. PATCH inactive product 400 `{"detail":"Product is no longer available."}`; shop 400 `{"detail":"Shop is currently unavailable."}`; stock uses the same stock error. Quantity validation is unwrapped, e.g. `{"quantity":["Ensure this value is greater than or equal to 1."]}` or `{"quantity":["A valid integer is required."]}`.

Common protected DRF endpoint errors: missing JWT 401 `{"detail":"Authentication credentials were not provided."}`; wrong customer role 403 `{"detail":"Customer access required."}`; blocked customer 403 `{"detail":"This customer account is blocked."}`. Expired/invalid JWT returns SimpleJWT's 401 token-error object (including its `code` and potentially `messages`); clients must not depend on one fixed token-error string. Generic object 404 messages may include the model name from DRF/Django; explicit shop detail errors use `{"detail":"Order not found."}` or `{"detail":"This order is awaiting Super Admin review."}`. Unsupported methods produce 405 `{"detail":"Method \"METHOD\" not allowed."}`. Do not assume resolver 404 or unhandled 500 has these DRF JSON shapes.

Sources: `customers/checkout_eligibility.py:14-90`; `customers/views.py:270-307,351-365,479-493,517-749`; `orders/views.py:76-245`; `shops/fields.py:6`; `accounts/authentication.py:8`.

## 7. Order statuses, transitions and tracking

Exact Order.status enum: `pending`, `pending_super_admin`, `admin_approved`, `accepted`, `rejected`, `confirmed`, `preparing`, `ready`, `completed`, `cancelled`. Exact order payment enum: `pending`, `paid`, `failed`, `refunded`. Payment method enum: `online`. Pickup mode model enum: `walk_in`, `vehicle` (new checkout accepts only vehicle).

| Actor/action | From | To | Gate / stock behavior |
|---|---|---|---|
| Customer create | Cart | pending / pending_super_admin | Reserve by immediate stock decrement |
| Super Admin approve | pending_super_admin / pending | admin_approved | Approval timestamp/user; no stock change |
| Super Admin approve historical active state | admin_approved/accepted/confirmed/preparing/ready | Same status | Sets approval metadata |
| Super Admin reject active unapproved high value | Any HIGH_VALUE_APPROVAL_PENDING_STATUSES | cancelled | Restore stock once |
| Shop accept or accepted decision | pending/admin_approved | confirmed | Requires high-value approval if applicable; no payment needed |
| Shop rejected decision | pending/admin_approved | rejected | Valid reason code; restore stock |
| Shop legacy reject | pending | cancelled | Fixed stock-unavailable message; restore stock |
| Customer cancel | pending/pending_super_admin/admin_approved/accepted/confirmed | cancelled | Restore stock; captured payment queues refund |
| Shop status preparing | confirmed or legacy accepted | preparing | Verified captured ledger and approval/shop checks |
| Shop status ready | preparing | ready | Same paid/approval/shop checks |
| Owning shop verifies OTP | ready | completed | Verified captured ledger, approval/shop, active matching OTP |

Customer cancellation from `preparing`, `ready`, `completed`, `rejected`, `cancelled` returns 409, e.g. `{"detail":"Order cannot be cancelled because its status is 'ready'."}`. It is not limited to unpaid orders. Payment/refund status is independent of order state; cancellation success does not mean a completed refund. Existing terminal guards plus order locks prevent repeated stock returns through these endpoints.

`accepted` remains a legacy status; current acceptance writes `confirmed` directly. Shop `/pending/` does not move an order back to pending. `/complete/` cannot bypass OTP. Model status choices are not a centralized transition engine or DB transition constraint.

Tracking response exact shape:

```json
{"order_id":41,"status":"confirmed","status_display":"Confirmed","current_step":2,"tracking_steps":[{"status":"pending","label":"Order Placed"},{"status":"accepted","label":"Order Accepted"},{"status":"confirmed","label":"Order Confirmed"},{"status":"preparing","label":"Preparing"},{"status":"ready","label":"Ready for Handoff"},{"status":"completed","label":"Completed"}],"created_at":"2026-11-17T06:00:00Z","updated_at":"2026-11-17T06:01:00Z"}
```

`current_step` is zero-based. For pending_super_admin, steps are pending_super_admin (Waiting for Super Admin), admin_approved (Approved by Super Admin), then accepted, confirmed, preparing, ready, completed. For admin_approved, the list starts at admin_approved. Rejected/cancelled use two steps: pending (Order Placed), then terminal state (Order Rejected/Order Cancelled). After confirmation, approval steps disappear from this projection; it is not a complete event history. Render server-provided steps/status rather than assuming every step is visited.

Sources: `orders/models.py:8-35`; `orders/views.py:353-415,696-810`; `shop_admin/api_views.py:707-739,795-872,1021-1103`; `shop_admin/super_admin_views.py:1450-1493`.

## 8. Super Admin approval flow

Fixed threshold comes from `orders/constants.py:4`; it is **₹15,000 total including the ₹50 fee**, so subtotal ₹14,950 qualifies exactly, ₹14,949 does not. No rule change is proposed. `order_requires_high_value_approval` also requires `super_admin_approved_at is None`; it protects processing even if a legacy order has a non-review status.

The pending-approval list exposes only active, unapproved high-value orders. Approval is atomic under shop/order locks, sets approved_by/at, writes SuperAdminAuditLog, and creates customer plus ShopAdminNotification records. Reject requires a nonempty reason, restores stock under product locks sorted by product ID, sets `cancelled`, writes audit metadata, and notifies. A repeated decision returns 400, not a repeat 200. Reason is recorded in audit metadata, not Order.reject_reason_code for this path. No payment is collected during approval; current normal payments cannot start until Shop Admin confirms the approved order. Historical paid orders and late captures on cancelled orders use the refund ledger/signal behavior.

Shop queue/detail hide or deny unapproved high value; accept/reject/decision/status/complete/pending and handover additionally check approval. No source-level Shop Admin approval bypass was found.

HighValueSummary exact fields: `id,order_number,status,shop:{id,shop_name},customer:{id,email,phone_masked},subtotal,application_fee,platform_fee,total_amount,payment_status,created_at`. Monetary values are strings; email/phone are masked. Here `order_number` is `str(id)`, **not** the `DRQ-` format. Detail adds `items:[{product_name,quantity,unit_price,subtotal}]`, `approval_history:[{id,actor_id,actor_email,action,summary,created_at}]`, `decision_state` (`pending`, `approved`, `rejected`, `not_pending`).

PendingApproval exact fields: `id,order_number,status,shop_id,shop_name,customer_name,subtotal,application_fee,platform_fee,total_amount,pickup_mode,created_at`. Its order_number uses `DRQ-`. High-value list pagination defaults page_size=20, supports 1..100; envelope is `count,next,previous,results`. Invalid page/page_size/status is 400. Pending-approval list returns count/results only and ignores pagination.

Restricted-shop review is a separate snapshot-based authorization for certain existing orders after suspension/license expiry. It does not reopen a shop for new checkout and does not replace high-value approval. There is a partial integration gap: accept/decision still use IsApprovedActiveShopAdmin and reject an expired/suspended shop even after existing-order review. See F5.

## 9. Shop Admin action JSON and handover

Use `/decision/` for the primary accept/reject integration. Allowed rejection reason codes are exactly `out_of_stock`, `shop_closed`, `unable_to_fulfil`, `other`. Accepted decision must not include a nonempty reason_code.

```json
{"decision":"accepted"}
```

200 Decision response example:

```json
{"id":41,"order_number":"DRQ-0041","status":"confirmed","decision":"accepted","reason_code":null,"notification_created":true,"customer_message":"Your order has been accepted. Our shop staff will hand over your order at your vehicle in approximately 10 minutes.","decided_at":"2026-11-17T06:01:00Z"}
```

Rejection request `{"decision":"rejected","reason_code":"shop_closed"}` returns the same fields with `status:"rejected"`, decision/reason changed and `customer_message:"Sorry, we are unable to accept your order because the shop is closed."`. Other messages: out_of_stock = `Sorry, we are unable to accept your order because the item is out of stock.`; unable_to_fulfil/other = `Sorry, we are unable to accept your order at this time.`.

Other action responses:

```json
{"detail":"Order accepted successfully.","order_id":41,"status":"confirmed","message":"Your order has been accepted. Our shop staff will hand over your order at your vehicle in approximately 10 minutes."}
```

```json
{"detail":"Order rejected successfully.","order_id":41,"status":"cancelled","reason":"Stock unavailable"}
```

```json
{"detail":"Order status updated successfully.","order_id":41,"status":"preparing"}
```

```json
{"detail":"Order is still awaiting handover.","order_id":41,"status":"confirmed"}
```

The second object is the legacy `/reject/` response, not `/decision/`. State progression errors include 400 `{"detail":"Invalid order status transition.","current_status":"pending","requested_status":"preparing"}`; unpaid processing is 402 `{"code":"payment_required","detail":"Verified customer payment is required before preparing the order."}`. Decision state conflicts are 409 `{"detail":"This order cannot be decided in its current status."}`; high-value decision conflicts are 409 `{"detail":"Super Admin approval is required before this order can be decided."}`. Invalid decision is 400 `{"decision":["Choose accepted or rejected."]}`; invalid rejection reason is 400 `{"reason_code":["Choose a valid rejection reason code."]}`.

Handover Challenge response fields are exactly:

```json
{"order_id":41,"status":"ready","otp_state":"active","challenge_id":"11111111-1111-4111-8111-111111111111","otp":"012345","expires_at":"2026-11-17T06:20:00Z","server_time":"2026-11-17T06:10:00Z","attempts_remaining":5,"reissue_available_at":"2026-11-17T06:11:00Z"}
```

OTP states: `not_issued`, `active`, `consumed`, `locked`, `expired`, `unavailable`. Nonactive responses have `otp:null`; challenge/time fields are null before issuance. GET before readiness returns `unavailable`, not a new challenge. Challenge TTL 10 minutes; reissue cooldown 60 seconds; max three issues/reissues in rolling hour; five verification attempts. Codes are customer-visible only, encrypted at rest with independent HMAC digest and versioned keys. A UUID plus code binds verification to the order and active challenge. Completed/cancelled/rejected clears secrets and live coordinates.

Shop submits `{"challenge_id":"11111111-1111-4111-8111-111111111111","otp":"012345"}`. HandoverSuccess 200:

```json
{"message":"Vehicle handover verified successfully.","order_id":41,"status":"completed","pickup_verified":true,"pickup_verified_at":"2026-11-17T06:12:00Z","pickup_verified_by":8}
```

Handover errors use `{"code":code,"detail":message}`. Exact examples: 409 `{"code":"order_not_ready","detail":"Order must be ready and awaiting vehicle handover."}`; 409 `{"code":"stale_challenge","detail":"Refresh the current handover challenge."}`; 422 `{"code":"invalid_handover_otp","detail":"The handover code is incorrect.","attempts_remaining":4}`. Expired/locked states use `handover_otp_expired`/`handover_otp_locked` and `The handover code is unavailable; the customer must reissue it.`. Missing secure keys gives 503 `{"code":"handover_unavailable","detail":"Secure handover is temporarily unavailable."}`. Durable issuance throttles include `retry_after` and Retry-After. Failed attempts commit; successful verification and audit/notification commit atomically. Payment and handover responses set no-store/private and vary on Authorization.

Location response is `{"order_id":41,"is_sharing":false,"latitude":null,"longitude":null,"updated_at":null}` when absent. Sharing responses contain numeric coordinates and an ISO timestamp. Coordinates expire after 60 seconds and are cleared on terminal order states. **Only Shop Admin publishes location**; customer POST is forbidden. This is not a customer vehicle-location publishing API and must not be described as one in Flutter.

## 10. Inventory and payments

### Inventory

Placement immediately deducts stock, even before approval, shop acceptance or payment. Cart and preview do not reserve. No reservation record, expiration timestamp, unpaid-order timeout or automatic replenishment worker was found. Pending/unpaid/high-value orders can hold inventory indefinitely until cancellation/rejection. Handover/payment does not deduct again. Customer cancellation, Shop Admin rejection and Super Admin rejection restore item quantities. Only `/decision/` rejection creates StockMovement entries; creation and other return paths do not provide a consistent stock movement ledger.

Checkout-to-checkout stock contention uses locked fresh product rows and cannot simply oversell by reading the same old stock. This guarantee does not extend to unlocked manual adjustment/product PATCH paths (F1). Order locks protect same-order terminal actions against repeated restoration. Lock order is not universal across older endpoints (F6).

### Gateway and collection gates

Effective local settings were inspected by printing only nonsecret mode/enable flags and DB engine: `gateway_mode=disabled`, `test_enabled=False`, PostgreSQL. No credentials were printed. Adapter allows only explicitly enabled **test** credentials (`rzp_test_...`); live keys are rejected in code. No configuration was changed and no real processing enabled.

The ₹50 application/platform fee is included once in total; payment total is subtotal + fee. Payment ledger stores integer paise and an INR-only amount split. Razorpay Route destination is a server-managed enabled ShopPaymentAccount, not customer input. Missing shop onboarding blocks initiation. Preparation/ready/OTP require both Order.payment_status paid and matching captured ledger with provider IDs, capture time, no refund and exact amount split. A manually set `paid` flag does not satisfy this.

`OrderPayment` is OneToOne with Order; unique provider order/payment/refund IDs prevent cross-ledger reuse. Ledger states exactly: `creating`, `created`, `captured`, `refund_required`, `refunding`, `refunded`, `review`. PaymentRefund states: `requested`, `pending`, `processed`, `failed`, `review`. Operational case kinds: `reconciliation`, `refund`, `settlement`, `dispute`; statuses `open`, `review`, `resolved`. These are distinct from Order.payment_status. A signed failed payment records last_error but does not set the Order status to failed; Flutter should not expect every model enum to be emitted in every path.

PaymentPublic (initiate 201/200, verify/reconcile 200) example:

```json
{"order_id":41,"payment_status":"pending","payment_state":"created","gateway":"razorpay","mode":"test","amount":55000,"currency":"INR","platform_fee":"50.00","checkout":{"key":"rzp_test_EXAMPLE","order_id":"order_EXAMPLE","amount":55000,"currency":"INR","name":"DRINQO"}}
```

`checkout` is only included for initiation when ledger is created and order confirmed/accepted; verification/reconciliation return the same public fields without checkout. For captured verification, payment_status is paid and payment_state captured. Request verification signature must be exactly 64 lowercase hex characters; provider IDs must match `order_`/`pay_` followed by alphanumerics, max length 64. Customer payment success callbacks alone never mark paid: HMAC binding and provider-fetched capture/order/amount/currency evidence are validated.

Exact default local response to valid payment initiation is **503** `{"code":"payment_unavailable","detail":"Payment service is unavailable."}` because gateway construction fails before order lookup. After authorized test configuration, endpoint errors include:

| HTTP | code | detail |
|---|---|---|
| 409 | order_not_payable | The shop must confirm the order before payment. |
| 403 | approval_required | Super Admin approval is required before payment. |
| 403 | shop_restricted | This order requires Super Admin shop review. |
| 503 | shop_payments_unavailable | Shop payment onboarding is incomplete. |
| 409 | payment_configuration_changed | Payment reconciliation is required. |
| 409 | creation_outcome_unknown | Payment initialization requires reconciliation. |
| 404 | payment_not_found | Payment not found. |
| 400 | invalid_payment_signature | Invalid payment verification. |
| 409 | payment_not_captured | A matching captured payment is required. |
| 409 | payment_not_captured | Payment is still being confirmed. |
| 409 | multiple_captures | Payment reconciliation is required. |
| 400 | invalid_webhook_signature | Invalid webhook verification. |
| 400 | invalid_webhook | Invalid webhook. |
| 503 | payment_not_found | Payment mapping not found; retry later. |
| 400 | webhook_identity_conflict | Webhook reconciliation is required. |

Each table row is the exact `{"code":...,"detail":...}` body. Provider transport/validation errors also surface fixed safe GatewayError messages; clients must handle non-2xx by code/status, not assume this business-error table enumerates all network failures. Strict empty-body payment requests with unknown fields return 400 `{"non_field_errors":["Unexpected request fields."]}`. Throttling can return 429 standard DRF detail with a dynamic wait interval.

Payment-status response fields: `order_id,payment_method,payment_status,subtotal,application_fee,platform_fee,total_amount,ledger_status,refund_status,reconciliation_required,payment_eligible,next_action`. Its amounts are JSON numbers because it directly constructs a Response from Decimal values. Projection `next_action` values in source are `await_shop_confirmation`, `await_shop_preparation`, `refund_under_review`, `payment_under_review`, `complete_payment`, `await_order_eligibility`. Do not use these as order statuses. The projection has known eligibility bugs (F2).

### Replay, cancellation and refunds

Initiation persists a ledger marker before remote CREATE. Repeated creation reuses the same provider order or searches by stable receipt; an ambiguous CREATE is not blindly repeated. Provider calls occur outside database locks. Capture commits under shop/order/payment locks. Webhooks verify HMAC over raw bytes, require bounded event IDs, deduplicate unique event IDs and payload digests, and commit capture/notification/dedupe atomically. Altering a replay payload conflicts. Refund/capture replay logic does not resurrect refunded or terminal orders. Unsupported authenticated webhook event types return received true without processing.

Cancellation/rejection signals mark captured ledger `refund_required`; an on_commit callback attempts a full-total refund, including the ₹50 fee. Network failure does not roll back cancellation; it records review liability. Late capture after cancellation queues refund rather than reviving order. A persisted refund start marker and receipt-based recovery avoid blind repeated refund POSTs. Partial refunds enter review and cannot authorize fulfillment. No customer refund-request API or claim that refunds are instant should be added to Flutter.

There is an operational recovery limitation: `try_refund_order` converts failure to `review`, while `reconcile_operational_payment` only opens a reconciliation case for review and returns without retrying/recovering the refund. The named Super Admin retry path and management command call that function. See F7.

Super Admin payment operations exist: GET `/api/super-admin/payments/` returns paginated **orders** with fields `id,order_id,order_number,shop:{id,shop_name},amount,payment_status,payment_method,created_at`; filters `payment_status,order_id,shop_id,from,to,search,page,page_size` (dates YYYY-MM-DD). GET `/payments/{payment_uuid}/` returns `payment_id,order_id,shop_id,currency,amount,shop_amount,platform_fee,ledger_status,order_payment_status,refunds,cases,projection,created_at,updated_at`; amounts here are paise. GET `/payments/cases/` supports `kind,status,page,page_size`, returning paginated `id,payment_id,order_id,kind,status,reason_code,created_at`. POST `/payments/{uuid}/cases/` requires `kind,reason_code`, optional `external_reference`, returning 201 new/200 existing `id,kind,status,created`. POST `/payments/{uuid}/refund/retry/` returns payment detail after attempted operational reconciliation; rejects non-reviewable state with 400. All these are Super Admin JWT only; they cannot force a payment to paid. Case creation and retry write audit records. These operations were inspected only, never invoked.

Sources: `orders/payments.py:36-73,115-241,244-385,387-574`; `orders/models.py:280-386`; `orders/signals.py:8-33`; `orders/razorpay_gateway.py:80-120`; `config/settings.py:457-484`; `shop_admin/super_admin_views.py:502-676`; `orders/management/commands/reconcile_payments.py:27`.

## 11. Confirmed bugs, missing functionality and security risks

Severity describes impact and required conditions. No Critical exploit was confirmed by this read-only source review. Confirmed implementation defects are separated from unexecuted concurrency scenarios and product-policy gaps.

| ID | Severity / confidence | Finding, evidence and impact | Minimal recommendation (not implemented) |
|---|---|---|---|
| F1 | **High**, confirmed unsafe stock writer; race not executed | `shop_admin/api_views.py:339-384` reads/modifies/saves stock without atomic row lock; product PATCH at `:557-588` saves an unlocked instance and exposes stock in `api_serializers.py:69-70`. Example: admin reads 10, checkout deducts to 8, stale admin adjustment +1 writes 11 instead of 9. Default ModelSerializer update can also save stale stock while changing another field. Checkout's own locks cannot protect against stale calculations performed before those locks. StockMovement can fail after adjustment is committed. | Make all product stock mutation paths atomic and use locked fresh rows or guarded atomic arithmetic; keep movement records in the same transaction. |
| F2 | **High** integration blocker, confirmed | `orders/payments.py:41-47` reports payment_eligible false and await_shop_confirmation whenever no ledger exists, including a freshly confirmed order. Flutter following that field will never call initiate and the ledger can never be created. Existing-ledger eligibility at `:58` also omits high-value approval, shop eligibility and customer eligibility enforced by `_check_payable`. | Derive projection from the same eligibility policy before/after ledger creation; expose accurate allowed action without needing initialization first. |
| F3 | **Medium**, confirmed | Legacy `/reject/` (`shop_admin/api_views.py:851-889`) changes state to cancelled, permits only pending, ignores reason, and lacks decision metadata. `/decision/` permits admin_approved, records reason/time/user and writes rejected (`:707-758`). An approved high-value order cannot be rejected by the legacy route. | Integrate Flutter with decision now; later consolidate legacy route onto the same service/contract. |
| F4 | **High**, confirmed source sequence | Age API writes User DOB only (`accounts/views.py:568-589`); customer-profile update derives requested DOB from profile DOB even if DOB omitted (`customers/views.py:101-122`). With User DOB set by age API and profile DOB null/stale, a later address-only PATCH can null/change User DOB and clear declaration, causing repeated DOB/Profile Setup demands. | Only synchronize DOB when explicitly supplied; unify authoritative DOB serialization or synchronize both stores deliberately. |
| F5 | **Medium**, confirmed | Existing-order shop-review allows continuation (`super_admin_views.py:1324-1386`), but accept and decision require IsApprovedActiveShopAdmin (`api_views.py:685,769`) which unconditionally denies expired/suspended shops (`shops/permissions.py:111`). A reviewed pending order remains unable to reach confirmed. | Apply reviewed existing-order eligibility consistently under locks while retaining rejection of new orders for restricted shops. |
| F6 | **Medium**, concurrency risk inferred from lock graph | Shared helper locks shop then order (`orders/locking.py:7-14`). Customer cancellation locks order then products (`orders/views.py:359-395`) and legacy accept/reject lock order before joined item/product/shop queries (`api_views.py:778,804,834,864`). Checkout holds shop then products. Older joined `FOR UPDATE` can lock related shop/product rows after an order lock, creating a potential inversion against shop-first operations; product return order is not always deterministic. No contention run was authorized. | Use one shop -> order/cart as appropriate -> sorted products locking policy and scope joined row locks explicitly; test on isolated PostgreSQL. |
| F7 | **High** financial operations gap, confirmed | Refund failure moves ledger to review (`orders/payments.py:469-487`); operational reconciliation for review merely creates a case and returns (`:489-504`). Super Admin refund retry (`super_admin_views.py:652-676`) and reconciliation command cannot recover that refund through this path. Review ledger is excluded from customer refund attempts too. | Provide an audited recovery path that first reconciles provider refund evidence and safely resumes only when justified; never blindly repeat POST. |
| F8 | **Medium**, confirmed missing feature | No create-order idempotency token/replay record (`orders/views.py:73-323`, Order model). Parent-cart locking handles ordinary double clicks but does not return the first result on retry; timeout plus repopulated cart can produce a second order. | Add customer-scoped unique idempotency key with request fingerprint and stored result; retain cart serialization. |
| F9 | **Medium**, confirmed missing lifecycle | Stock deducted on placement (`orders/views.py:277-283`) with no reservation expiry/unpaid cancellation worker. Disabled gateway means unpaid confirmed orders cannot progress and can retain inventory indefinitely. | Define reservation lifetime and safe terminal release; explicitly communicate current payment unavailability. Do not enable live payment as an audit fix. |
| F10 | **Medium**, trust/policy gap | Coordinates supplied by client authorize 100m placement (`orders/views.py:229-245`), are not saved, and handover checks no distance (`orders/handover_otp.py:88-97,198-238`). Valid numbers do not prove physical presence. | Clarify whether 100m is a placement rule or actual handover rule; if latter, persist/refresh handoff evidence and enforce it at handover. |
| F11 | **Low**, confirmed synchronization gap | Cart DELETE (`customers/views.py:502-509`) does not use parent-cart lock. Concurrent checkout may already have read/reserved an item while a user removes it; deletion is not a serialized cancellation of checkout intent. | Serialize DELETE with the same parent cart lock; expose a cart version if preview consistency is required. |
| F12 | **Low**, confirmed audit/contract inconsistency | Stock movement ledger incomplete across placement/returns; numeric-vs-string money varies by endpoint; Super Admin order_number differs (`super_admin_views.py:355` vs `:1279`); generic Order response omits pickup_verified and decision reason. | Standardize documented DTOs and inventory events; add required fields only through reviewed contract updates. |
| F13 | **Low** local configuration finding | Both checks emit a development JWT signing-key length warning (`config/settings.py:62-80`). No key value was printed. This is not evidence of a production deployment using that key. | Retain/enforce existing production key policy; validate deployment separately. |

Other verified protections and limits:

- Customer ownership filters cover cart, vehicle selection, history, detail, cancellation, payments and OTP; Shop Admin order access always derives its shop/owner. No customer-supplied customer/shop identifier can redirect placement. Super Admin APIs check user_type, not merely is_staff.
- Cart and order prices are server-derived. Product creation/update in the normal Shop Admin serializer rejects nonpositive prices. Order item prices are snapshots; old order totals do not follow later product prices.
- Stock availability, single-shop consistency and active products are rechecked at placement. No confirmed straightforward checkout-to-checkout oversell bug was found in the current locked path; untested external stock writers remain material.
- Checkout does not require a verified phone identity: presence of a profile/user phone is the actual rule. This is a **Medium policy gap if verified phone is a requirement**, not evidence that the implemented presence check is missing. Do not label self-declaration documentary age verification.
- Approval/decision requests validate allowed values, but several function views use sets on raw request values rather than serializers. A malicious JSON array/object for decision/status/reason can cause an unhandled unhashable-type error instead of 400 (`shop_admin/api_views.py:691-695`; `super_admin_views.py:1536-1538`). **Low reliability defect**; use typed request serializers.
- No order-creation-specific throttle, max quantity/order cap, lock timeout, retry/backoff or structured deadlock error is configured. **Medium availability risk** for repeated cart/checkout traffic; generic JWT authentication is not an abuse limit.
- Order responses do not include customer phone/address, payment secrets, OTP code or gateway signatures. Owning shop receives vehicle data and challenge ID, but OTP plaintext is only in the customer's dedicated no-store response. Super Admin masks customer phone/email in high-value summaries; audit history intentionally includes actor email.
- There is no verified delivery address or retained checkout coordinate in Order. There is no new walk-in checkout, cash/COD, customer-controlled paid update, automatic push delivery, or end-to-end reservation lifecycle in the inspected path. These are missing capabilities, not invented API fields.
- GET cart/profile can create records and GET live-location can clear stale coordinates (`orders/views.py:500-513`). Even a manual “read-only” HTTP smoke audit would not be guaranteed read-only; none was performed here.

## 12. Validation performed and existing tests

| Command | Result |
|---|---|
| `python manage.py check` using PATH Python | Exit 1: `ModuleNotFoundError: No module named 'dotenv'` before Django setup completed |
| `python manage.py makemigrations --check --dry-run` using PATH Python | Exit 1: same missing dependency |
| `.\venv\Scripts\python.exe manage.py check` | Exit 0: `System check identified no issues (0 silenced).` |
| `.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | Exit 0: `No changes detected` |

Both virtual-environment commands emit `Development JWT signing key is shorter than 32 bytes; configure SIMPLE_JWT_SIGNING_KEY for production.`. `PYTHONDONTWRITEBYTECODE=1` was set in the check processes to avoid bytecode writes; no project configuration was changed. Settings import was inspected and only nonsecret runtime payment mode, enable flag and DB engine were printed. No test runner was invoked. The dry run does not apply migrations; its pass proves model/migration consistency, not that every migration is applied on every deployment.

Inspected existing test sources include:

- `customers/tests.py:482-791`: invalid coordinates, minimum, fee, geofence, persisted self-declaration, shared eligibility errors, closed shops and order creation.
- `customers/test_customer_security_fixes.py:70`: vehicle checkout requirements.
- `orders/tests.py:219-347`: response contract, customer ownership, tracking, cancellation conflicts, role/authentication protection; further live-location privacy cases.
- `orders/test_inventory_concurrency.py:167-333`: checkout/rejection contention, competing checkout, duplicate returns, rollback, bounded lock-wait tests. Uses TransactionTestCase and PostgreSQL; inspected only. Its header mentions an older joined checkout risk; current create explicitly scopes cart row locks, so the header alone is not evidence that the old implementation remains.
- `orders/test_payments.py:181-574`: authoritative amounts, initiation reuse, high-value gate, forged callbacks, capture/refund replay, reconciliation, rollback and paid preparation/handover. `:584` adds PostgreSQL concurrency tests.
- `orders/test_payment_operations.py`: fee/paise arithmetic, projections, refund records, over-refund protection and operator role restrictions.
- `orders/test_handover_otp.py`, `test_handover_corrections.py`, `test_handover_retention.py`: OTP ownership, lifecycle, privacy and terminal handling.
- `shop_admin/tests.py`, `shop_admin/test_super_admin_api.py`, `shop_admin/test_shop_approval_permissions.py`: order/admin/approval-related cases; `accounts/test_age_self_declaration.py`: persisted declaration policy.

Presence of a test does not establish that it currently passes. No shared development database tests or contention reproductions were run. Follow-up validation should use an explicitly isolated PostgreSQL test database and a fake gateway, including new regression scenarios for F1, F2, F4, F5 and F7.

## 13. Minimal recommended fixes and required Flutter integration contract

Recommended order of reviewed work:

1. Fix all stock mutation writers (F1), misleading payment eligibility (F2), DOB fallback reset (F4), and safe refund-review recovery (F7). Preserve ₹15,000 inclusive-total approval rule and ₹50 fee.
2. Consolidate Shop Admin decision services and locking, including restricted-order review handling (F3/F5/F6). Keep OTP as the sole completion path.
3. Add order idempotency and an explicit reservation lifecycle (F8/F9); define and enforce the intended handover location policy (F10).
4. Normalize response DTOs, typed validation, audit movements and cart deletion synchronization. Verify each meaningful change with isolated regression/concurrency tests before deploying.

Required current Flutter behavior:

- Send `Authorization: Bearer <Django access JWT>` and JSON Content-Type. Refresh using the account token contract; never put tokens or OTPs in logs. Wrong-role/blocked errors must not cause an onboarding loop.
- Persist Personal Details once and save DOB/declaration through the correct APIs. Handle `profile_incomplete` using `missing_fields`; DOB/age errors are separate. Do not demand name/address again for every order or interpret documentary `proof_required` as necessarily blocking self-declared checkout. Account/profile DOB inconsistency F4 requires backend correction; merely repeating Profile Setup can worsen it.
- Save/select a customer-owned vehicle, send `vehicle_id` plus valid current coordinates to preview and create. New orders support vehicle handover only. Do not send client prices or expect address/delivery fields to be honored.
- Cart POST adds to existing quantity; PATCH replaces quantity; DELETE uses the separate `/delete/` route. Compare product shop against the visible shop and handle the server's mixed-shop error without silently replacing cart contents.
- Call `POST /api/customer/checkout/preview/`; check `can_checkout`, even on HTTP 200. Display server totals. Charge/display ₹50 once despite both fee aliases. Minimum applies before fee, high-value threshold after fee. Prices can change before create.
- Place via **`POST /api/orders/create/`**. Read bare response `id`, `order_number`, `status`, totals and items. Disable duplicate taps. A timeout/empty-cart retry is ambiguous: inspect order history/detail before rebuilding cart and retrying; there is no idempotency guarantee.
- Show `pending_super_admin` as waiting for approval. Shop Admin should query its own queue and use `/decision/`; high-value orders appear after approval. Keep `accepted` support for historical data but current acceptance yields `confirmed`.
- Payment occurs after confirmation. Current gateway is disabled: present payment unavailability accurately. Until F2 is fixed, `payment_eligible:false` is not a reliable indication that a confirmed order cannot initialize a ledger. Do not fake payment success or bypass paid gates to work around configuration.
- When test payments are explicitly available, initialize with `{}`, launch Razorpay using returned checkout fields/paise, verify returned provider IDs/signature on the backend, and reconcile lost callbacks. Payment success UI must follow server evidence. Payment and ledger states are separate from fulfillment status.
- Poll `/history/`, `/{id}/`, `/tracking/` and `/payment-status/`. Customer history and notifications use `count,next,previous,results` with page size 10. No order-status filter is implemented in history; no paging/filtering in Shop Admin order queue. High-value list uses separate page_size support. Parse monetary decimal strings and JSON numbers without binary-float charge calculations.
- Shop status is strictly confirmed/accepted -> preparing -> ready, and payment is required for both updates. Ready customer issues/reads OTP; Shop Admin verifies with challenge_id and six-character string code. Keep leading zeros, honor expiry/retry_after/attempts, refresh stale challenges and never expose OTP in shop list/detail.
- `/complete/` is not a completion action; `/verify-pickup/` returns authoritative completed/pickup_verified fields. Generic Order serializer does not include pickup_verified, so use verification response or order status for completed UI.
- Live-location GET is visible to customer and owning shop; only owning shop may publish. Coordinates become null when stale/finished. Do not implement customer POST location against this endpoint.
- Cancellation is allowed only before preparing and may trigger a later refund; show refund review/pending separately from cancellation. Handle 400 validation, 401 authentication, 402 payment, 403 role/approval/restriction, 404 ownership/resource, 409 state/conflict, 422 wrong OTP, 429 throttling and 5xx availability distinctly.

No implementation changes are included. This report is the review artifact for deciding follow-up work.
