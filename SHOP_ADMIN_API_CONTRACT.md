# DRINQO Shop Admin API Contract

This file captures the backend contract implemented in the live codebase as of 2026-10-22.

Payment source update: [Razorpay payments](./docs/razorpay-payments.md) describes
the disabled-by-default, Test Mode-only implementation, separate payment
ledger and refund handling. Migration `0010_payment_ledger.py` is unapplied;
tests for this change have not run and live payments are not activated. Order
responses add `platform_fee` as an alias of legacy `application_fee`: display
one ₹50 **Platform fee**, with no added delivery charge. Customers pay after
Shop confirmation; verified payment is required before preparation/handover.
See also [Vehicle handover OTP](./docs/phase-2a-handover-otp.md).

## Verification

I verified the backend with:

```bash
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test shop_admin.test_super_admin_api accounts customers orders products shops --verbosity 2
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Fresh result:
- `System check identified no issues (0 silenced).`
- `Ran 30 tests`
- `OK`
- `No changes detected` by the migration check.

---

## 1) Shared authentication and authorization contract

### Django JWT auth header
```http
Authorization: Bearer <Django access JWT>
Content-Type: application/json
```

### 401 Unauthorized
Returned when Django JWT credentials are missing, invalid, expired, or no
longer correspond to an active user.

```json
{
  "detail": "Authentication credentials were not provided."
}
```

### 403 Forbidden
Returned when current user is not an eligible Shop Admin. Shop editing
operations additionally require an approved shop with a current verified
license, active and not suspended.

```json
{
  "detail": "Shop Admin access required."
}
```

### 404 Not Found
Returned when no assigned shop exists for the current shop admin user.

```json
{
  "detail": "No shop is assigned to this Shop Admin."
}
```

---

## 2) GET /api/shop-admin/me/

### Success response
```json
{
  "user_id": 12,
  "email": "shopadmin@example.com",
  "user_type": "shop_admin",
  "shop": {
    "id": 5,
    "shop_name": "DRINQO Test Shop",
    "phone": "9876543210",
    "email": "shop@example.com",
    "address_line1": "22 Main Road",
    "address_line2": "",
    "city": "Pune",
    "state": "MH",
    "postal_code": "411001",
    "license_status": "verified",
    "is_active": true,
    "latitude": "18.520430",
    "longitude": "73.856744"
  }
}
```

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 3) PATCH /api/shop-admin/shop/

### Request JSON
```json
{
  "phone": "9876543211",
  "email": "newshop@example.com",
  "address_line1": "New Address",
  "city": "Mumbai"
}
```

Location can be updated with the same endpoint. Coordinates are optional decimal
values; latitude must be between `-90` and `90`, and longitude between `-180`
and `180`. Send both to change the full location:

The request requires `Authorization: Bearer <Django access JWT>`. The endpoint
is scoped to the authenticated Shop Admin's own shop and retains the existing
approved/active-shop permission requirement.

```json
{
  "latitude": "18.520430",
  "longitude": "73.856744"
}
```

### Success response
```json
{
  "detail": "Shop information updated.",
  "shop_id": 5,
  "updated_fields": [
    "latitude",
    "longitude"
  ],
  "shop": {
    "latitude": "18.520430",
    "longitude": "73.856744"
  }
}
```

Unknown request fields are rejected instead of silently ignored:

```json
{
  "detail": "Unknown fields.",
  "fields": ["is_active"]
}
```

Coordinate validation errors use the standard DRF field-error shape, for
example:

```json
{
  "latitude": ["Ensure this value is less than or equal to 90."]
}
```

### License renewal upload

Approved Shop Admins can submit a license renewal without changing the shop's
approval or activation state:

```http
POST /api/shop-admin/application/documents/
Authorization: Bearer <Django access JWT>
Content-Type: multipart/form-data
```

Fields: `license_number`, `expiry_date` (`YYYY-MM-DD`), and
`license_document` (PDF/JPG/JPEG/PNG, at most 10 MB). The upload is pending;
it does not make an otherwise ineligible shop visible to customers.

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 4) PATCH /api/shop-admin/shop/availability/

### Request JSON
```json
{
  "is_active": true
}
```

### Success response
```json
{
  "message": "Shop availability updated successfully.",
  "shop": {
    "id": 5,
    "shop_name": "DRINQO Test Shop",
    "is_active": true
  }
}
```

### Validation error
```json
{
  "detail": "Field 'is_active' is required and must be a boolean."
}
```

### Status codes
- 200 OK
- 400 Bad Request
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 5) GET /api/shop-admin/dashboard/

### Actual live response
```json
{
  "shop_id": 5,
  "shop_name": "DRINQO Test Shop",
  "shop_is_active": true,
  "license_status": "verified",
  "total_products": 12,
  "active_products": 11,
  "total_orders": 40,
  "todays_orders": 3,
  "total_product_value": "2450.00"
}
```

### Important note
The following fields are not implemented in the live backend and are not returned:
- `today_delivery_charges`
- `total_delivery_charges`

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 6) GET /api/shop-admin/notifications/

### Success response
```json
[
  {
    "id": 7,
    "notification_type": "new_order",
    "title": "Order update",
    "message": "A new order has arrived.",
    "order_id": 99,
    "is_read": false,
    "created_at": "2026-10-14T10:15:00Z"
  }
]
```

### Error responses
- 401 Unauthorized
- 403 Forbidden

---

## 7) POST /api/shop-admin/notifications/<notification_id>/read/

### Success response
```json
{
  "detail": "Notification marked as read.",
  "notification_id": 7,
  "is_read": true
}
```

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 8) GET /api/shop-admin/products/

### Success response
```json
[
  {
    "id": 1,
    "shop": 5,
    "shop_name": "DRINQO Test Shop",
    "name": "Premium Whisky",
    "brand": "Local",
    "category": "whisky",
    "description": "Bottle",
    "price": "499.00",
    "stock_quantity": 10,
    "image": null,
    "is_active": true,
    "created_at": "2026-10-14T09:00:00Z",
    "updated_at": "2026-10-14T09:00:00Z"
  }
]
```

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 9) POST /api/shop-admin/products/

### Request JSON
```json
{
  "name": "Premium Whisky",
  "brand": "Local",
  "category": "whisky",
  "description": "Bottle",
  "price": "499.00",
  "stock_quantity": 10,
  "image": null,
  "is_active": true
}
```

### Field requirement note
- `brand` is optional. It is defined with `blank=True` in [products/models.py](products/models.py) and is not marked as required by [shop_admin/api_serializers.py](shop_admin/api_serializers.py).
- `description` is optional. It is also defined with `blank=True` and is not required by the serializer.

### Success response
```json
{
  "id": 1,
  "shop": 5,
  "shop_name": "DRINQO Test Shop",
  "name": "Premium Whisky",
  "brand": "Local",
  "category": "whisky",
  "description": "Bottle",
  "price": "499.00",
  "stock_quantity": 10,
  "image": null,
  "is_active": true,
  "created_at": "2026-10-14T09:00:00Z",
  "updated_at": "2026-10-14T09:00:00Z"
}
```

### Validation rules
- `brand` optional; may be empty string
- `description` optional; may be empty string
- `price > 0`
- `stock_quantity >= 0`
- shop must be active and license verified before product creation

### Error responses
- 400 Bad Request (validation)
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 10) GET /api/shop-admin/products/<product_id>/

### Success response
Same as one product object from the list above.

### Error responses
- 404 Not Found
- 401 Unauthorized
- 403 Forbidden

---

## 11) PATCH /api/shop-admin/products/<product_id>/

### Success response
Updated product object in same shape as above.

### Validation
- `price <= 0` not allowed
- negative stock not allowed

### Error responses
- 400 Bad Request
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 12) DELETE /api/shop-admin/products/<product_id>/

### Success response
```json
{
  "detail": "Product archived successfully."
}
```

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 13) GET /api/shop-admin/inventory/

### Actual live response
This is the product list using the same product serializer shape as above.

```json
[
  {
    "id": 1,
    "shop": 5,
    "shop_name": "DRINQO Test Shop",
    "name": "Premium Whisky",
    "brand": "Local",
    "category": "whisky",
    "description": "Bottle",
    "price": "499.00",
    "stock_quantity": 10,
    "image": null,
    "is_active": true,
    "created_at": "2026-10-14T09:00:00Z",
    "updated_at": "2026-10-14T09:00:00Z"
  }
]
```

### Important note
The live backend does not return these fields:
- `reserved_quantity`
- `available_quantity`
- `low_stock_threshold`
- `is_low_stock`

`low_stock_threshold` is not a field in the live model at [products/models.py](products/models.py).

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 14) POST /api/shop-admin/inventory/<product_id>/adjust/

### Request JSON
```json
{
  "quantity_change": 5,
  "reason": "new_delivery"
}
```

### Success response
```json
{
  "message": "Inventory updated successfully.",
  "product_id": 1,
  "shop_id": 5,
  "quantity_change": 5,
  "previous_stock": 10,
  "stock_quantity": 15,
  "reason": "new_delivery"
}
```

### Exact allowed reason values
The live backend does not enforce a strict enum or choices list for `reason`.

In practice, the frontend contract values in Flutter are:
- `new_delivery`
- `damaged`
- `manual_correction`
- `return`

If `reason` is omitted, the backend returns the default value `manual` in the response, but it does not reject other strings. The server simply echoes the submitted value; it does not validate against a fixed allowlist.

### Validation errors
```json
{
  "detail": "Field 'quantity_change' is required."
}
```

```json
{
  "detail": "Field 'quantity_change' must be an integer."
}
```

```json
{
  "detail": "Adjustment would make stock negative."
}
```

### Status codes
- 201 Created on success
- 400 Bad Request for missing/invalid/negative result
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found when product does not belong to the shop or does not exist

---

## 15) GET /api/shop-admin/orders/

### Success response
```json
[
  {
    "id": 100,
    "order_number": "DRQ-100",
    "status": "pending",
    "customer_name": "Demo Customer",
    "customer_phone": "9876543212",
    "pickup_mode": "walk_in",
    "vehicle_registration": null,
    "handoff_location": null,
    "subtotal": "850.00",
    "application_fee": "50.00",
    "delivery_charge": "0.00",
    "total_amount": "900.00",
    "payment_method": "online",
    "payment_status": "pending",
    "created_at": "2026-10-14T10:15:00Z",
    "items": [
      {
        "id": 1,
        "product_name": "Premium Whisky",
        "quantity": 1,
        "unit_price": "499.00",
        "line_total": "499.00"
      }
    ],
    "can_decide": true,
    "reject_reason_code": null,
    "decided_at": null,
    "customer_notifications": []
  }
]
```

### Error responses
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found

---

## 16) POST /api/shop-admin/orders/<order_id>/accept/

### Success response
```json
{
  "detail": "Order accepted successfully.",
  "order_id": 100,
  "status": "confirmed",
  "message": "Your order has been accepted and will be delivered within 10 minutes."
}
```

### Customer notification text
The exact customer-facing text is:

```text
Your order has been accepted and will be delivered within 10 minutes.
```

### Error responses
- 400 Bad Request for not-pending orders
- 403 Forbidden for high-value order requiring super-admin approval
- 404 Not Found when order is not for this shop

---

## 17) POST /api/shop-admin/orders/<order_id>/reject/

Legacy compatibility route: it cancels a pending order. New clients should use
`POST /api/shop-admin/orders/<order_id>/decision/` with `decision: "rejected"`
and a supported `reason_code` for a recorded rejection. The terminal statuses
and allowed starting states are intentionally not interchangeable.

### Success response
```json
{
  "detail": "Order rejected successfully.",
  "order_id": 100,
  "status": "cancelled",
  "reason": "Stock unavailable"
}
```

### Error responses
- 400 Bad Request for not-pending orders
- 403 Forbidden for high-value order
- 404 Not Found for wrong shop

---

## 18) Remaining APIs status from the final requirement

### 1. GET /api/shop-admin/reports/?range=7d
Status: Not implemented.

Reason: no route in [shop_admin/urls.py](shop_admin/urls.py) and no report view in [shop_admin/api_views.py](shop_admin/api_views.py).

### 2. POST /api/shop-admin/products/<product_id>/image/
Status: Not implemented.

### 3. GET /api/shop-admin/licenses/
Status: Implemented in legacy profile flow.

Route exists in [shop_admin/urls.py](shop_admin/urls.py).

### 4. GET/PATCH /api/shop-admin/preferences/
Status: Not implemented.

### 5. Dashboard delivery fields requirement
Status: Not implemented.

Actual dashboard response does not include:
- `today_delivery_charges`
- `total_delivery_charges`

---

## 19) Actual model fields in live backend

### Product model ([products/models.py](products/models.py))
```python
id
shop
name
brand
category
description
price
stock_quantity
image
is_active
created_at
updated_at
```

### Shop model ([shops/models.py](shops/models.py))
```python
id
owner
shop_name
phone
email
address_line1
address_line2
city
state
postal_code
latitude
longitude
license_number
license_status
is_active
is_suspended_by_super_admin
created_at
updated_at
```

---

## 20) Final note for Flutter integration
Flutter integration should use only the fields shown above that are actually present in the live backend. The current backend does not expose `reserved_quantity`, `available_quantity`, `low_stock_threshold`, `is_low_stock`, `today_delivery_charges`, or `total_delivery_charges` in the active implementation.

---

## 21) Super Admin API

All `/api/super-admin/` endpoints require a valid Django access JWT and
`user_type == "super_admin"`. Invalid/missing credentials return 401; other
roles and blocked customer requests return 403. Platform-wide lists use
`page`/`page_size` pagination (default 20, maximum 100).

Implemented endpoints:

| Method | Endpoint |
| --- | --- |
| GET | `/api/super-admin/me/` |
| GET | `/api/super-admin/dashboard/` |
| GET | `/api/super-admin/notifications/` |
| POST | `/api/super-admin/notifications/<notification_id>/read/` |
| GET | `/api/super-admin/shop-applications/` |
| GET | `/api/super-admin/shop-applications/<application_id>/` |
| POST | `/api/super-admin/shop-applications/<application_id>/approve/` |
| POST | `/api/super-admin/shop-applications/<application_id>/reject/` |
| POST | `/api/super-admin/shop-applications/<application_id>/request-changes/` |
| GET | `/api/super-admin/shops/` |
| GET | `/api/super-admin/shops/<shop_id>/` |
| PATCH | `/api/super-admin/shops/<shop_id>/status/` |
| GET | `/api/super-admin/customers/` |
| GET | `/api/super-admin/customers/<customer_id>/` |
| PATCH | `/api/super-admin/customers/<customer_id>/status/` |
| GET | `/api/super-admin/high-value-orders/` |
| GET | `/api/super-admin/high-value-orders/<order_id>/` |
| POST | `/api/super-admin/high-value-orders/<order_id>/approve/` |
| POST | `/api/super-admin/high-value-orders/<order_id>/reject/` |
| GET | `/api/super-admin/reports/?range=7d|30d|90d` |
| GET | `/api/super-admin/audit-logs/` |

Implementation notes:

- Existing `shops.Shop` rows act as applications via `license_status`.
  License documents and a separate application-status-history model are not
  linked to this shop table; application document lists are therefore empty,
  and status history is derived from immutable Super Admin audit events.
- High-value orders use the shared backend threshold in
  `orders/constants.py`. Approval returns the order to the existing
  Shop Admin `pending` workflow; rejection cancels it and restores reserved
  stock in a transaction.
- Customer blocking rejects password login and token refresh, and prevents
  authenticated customer requests, including order placement. Customer phone
  numbers and emails are masked in Super Admin customer/order summaries.
- An expiry date on the primary shop model, a distinct
  Shop Admin assignment model, and a platform-settings model do not exist.
  License-expiry metrics, assignment APIs, and settings
  APIs are not implemented. Dashboard fields are limited to available data.
- Shop suspension is recorded separately from shop availability so a Shop
  Admin cannot reactivate a platform-suspended shop.

## 22) Super Admin request and response examples

The examples below show the fields returned by the live views. List endpoints
use the common pagination envelope shown in the examples. Dates are ISO 8601;
report dates use India local time.

### Identity, dashboard, and notifications

`GET /api/super-admin/me/`

```json
{
  "id": 1,
  "email": "superadmin@example.com",
  "user_type": "super_admin",
  "is_active": true
}
```

`GET /api/super-admin/dashboard/`

```json
{
  "active_shops": 0,
  "pending_shop_applications": 0,
  "suspended_shops": 0,
  "today_orders": 0,
  "today_gmv": "0.00",
  "high_value_orders_pending": 0,
  "unread_notifications": 0
}
```

`GET /api/super-admin/notifications/`

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 7,
      "type": "general",
      "title": "High-Value Order Pending",
      "message": "Order #99 requires Super Admin review.",
      "resource_type": "order",
      "resource_id": 99,
      "is_read": false,
      "created_at": "2026-10-21T10:00:00Z"
    }
  ]
}
```

`POST /api/super-admin/notifications/7/read/` returns:

```json
{
  "id": 7,
  "is_read": true
}
```

### Shop applications and platform shops

`GET /api/super-admin/shop-applications/?status=pending&page=1&page_size=20`
returns:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 5,
      "shop_name": "DRINQO Test Shop",
      "owner_name": "Shop Owner",
      "owner_email": "owner@example.com",
      "phone": "9876543210",
      "city": "Pune",
      "state": "MH",
      "license_number": "FL-II/123",
      "license_status": "pending",
      "application_status": "pending",
      "submitted_at": "2026-10-21T10:00:00Z",
      "documents": []
    }
  ]
}
```

`GET /api/super-admin/shop-applications/5/` returns the same application
fields plus:

```json
{
  "license_details": {
    "license_number": "FL-II/123",
    "license_status": "pending"
  },
  "status_history": []
}
```

Requests:

```json
{"note": "License verified."}
```

for `POST /api/super-admin/shop-applications/5/approve/`;

```json
{"reason": "License document is invalid.", "note": "Upload a valid license."}
```

for `POST /api/super-admin/shop-applications/5/reject/`; and

```json
{"reason": "Address proof is missing."}
```

for `POST /api/super-admin/shop-applications/5/request-changes/`.
Approval returns:

```json
{
  "id": 5,
  "application_status": "verified",
  "license_status": "verified",
  "is_active": true
}
```

Requesting changes returns
`{"id":5,"application_status":"rejected"}`; this is the existing
correction-required state. The owner can resubmit through
`POST /api/shop-admin/application/`, after which the status returns to
`pending` and the application re-enters the review queue.

`GET /api/super-admin/shops/?status=active&license_status=verified` returns a
paginated list with this result item:

```json
{
  "id": 5,
  "shop_name": "DRINQO Test Shop",
  "city": "Pune",
  "state": "MH",
  "is_active": true,
  "is_suspended_by_super_admin": false,
  "license_status": "verified",
  "owner": {
    "id": 12,
    "name": "Shop Owner",
    "email": "owner@example.com"
  }
}
```

`GET /api/super-admin/shops/5/` returns that shop item plus
`license_number`, `is_suspended_by_super_admin`, `product_count`, `order_count`,
and `status_history`.
`PATCH /api/super-admin/shops/5/status/` accepts:

```json
{"is_active": false, "reason": "License expired."}
```

and returns:

```json
{"id": 5, "is_active": false}
```

### Customers

`GET /api/super-admin/customers/?status=active&page=1&page_size=20` and
`GET /api/super-admin/customers/22/` use this safe customer shape:

```json
{
  "id": 22,
  "email": "c***@example.com",
  "phone_masked": "******3210",
  "is_active": true,
  "is_blocked": false,
  "age_verified": true,
  "order_count": 12,
  "created_at": "2026-10-01T10:00:00Z"
}
```

`PATCH /api/super-admin/customers/22/status/` accepts:

```json
{"is_blocked": true, "reason": "Policy violation."}
```

and returns the same customer shape reflecting the new `is_blocked` value.

### High-value orders

`GET /api/super-admin/high-value-orders/?status=pending&page=1&page_size=20`
returns a paginated list using this item shape:

```json
{
  "id": 99,
  "order_number": "99",
  "status": "pending_super_admin",
  "shop": {"id": 5, "shop_name": "DRINQO Test Shop"},
  "customer": {
    "id": 22,
    "email": "c***@example.com",
    "phone_masked": "******3210"
  },
  "subtotal": "14950.00",
  "application_fee": "50.00",
  "total_amount": "15000.00",
  "payment_status": "pending",
  "created_at": "2026-10-21T10:00:00Z"
}
```

`GET /api/super-admin/high-value-orders/99/` returns this order shape plus
`items` (product-name/quantity/unit-price/subtotal snapshots),
`approval_history`, and `decision_state`.
The default `status=pending` results may include either
`pending_super_admin` orders or active legacy orders that meet the fixed
₹15,000 threshold on `total_amount` and have no Super Admin approval
timestamp. Shop Admin processing is blocked for these orders until approval.

Requests:

```json
{"note": "Approved after compliance review."}
```

for `POST /api/super-admin/high-value-orders/99/approve/`; and

```json
{"reason": "Compliance review failed.", "note": "Additional verification required."}
```

for `POST /api/super-admin/high-value-orders/99/reject/`.
Approval returns:

```json
{"order_id": 99, "status": "admin_approved", "decision": "approved"}
```

Rejection returns `"status": "cancelled"` and `"decision": "rejected"`. The
₹15,000 threshold is applied to `total_amount`, including the application fee;
the fee remains separate from the ₹1,000 minimum product subtotal.

### Reports and audit logs

`GET /api/super-admin/reports/?range=7d` returns:

```json
{
  "range": "7d",
  "orders_count": 0,
  "gmv": "0.00",
  "active_shops": 0,
  "new_shops": 0,
  "new_customers": 0,
  "order_trend": [],
  "top_shops": [],
  "category_trends": []
}
```

When data exists, `order_trend` includes one item per date in the requested
range and `top_shops` includes real aggregates.
`GET /api/super-admin/audit-logs/?actor_id=1&action=shop_suspended&resource_type=shop&page=1&page_size=20`
returns:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 100,
      "actor_id": 1,
      "actor_email": "superadmin@example.com",
      "action": "shop_suspended",
      "resource_type": "shop",
      "resource_id": 5,
      "summary": "Suspended DRINQO Test Shop.",
      "created_at": "2026-10-21T10:00:00Z"
    }
  ]
}
```

## 23) Model relationships used

- `accounts.User` carries `user_type`, `is_active`, `is_blocked`, and
  `firebase_uid`. Super Admin authorization checks the persisted
  `user_type`; blocked-customer checks apply only to `user_type="customer"`.
- `shops.Shop.owner` is a one-to-one relation to the auth user model.
  Application approval updates that shop's license and active state; platform
  suspension also sets `is_suspended_by_super_admin`.
- `products.Product.shop` points to `shops.Shop`. Product counts in shop
  details use this relation.
- `orders.Order.customer` points to the auth user, and `orders.Order.shop`
  points to `shops.Shop`. `orders.OrderItem` relates an order to its product
  and retains the product-name/price/quantity/subtotal snapshot used for
  Super Admin order details.
- `customers.Notification.user` scopes notifications to the recipient and
  its optional `order` relation supplies the resource reference.
- `shop_admin.ShopAdminNotification.shop_admin` points to the auth user;
  existing `order_id` is an integer field.
- `shop_admin.SuperAdminAuditLog.actor` points to the auth user with
  `PROTECT`. Resource type/id are stored as an immutable generic reference,
  so deleting or changing a shop/order/customer is not required to retain its
  audit event.
- The legacy `shop_admin.Shop`/`ShopLicenseDocument` tables relate to
  `ShopAdminProfile`, not `shops.Shop`; no direct relationship exists to the
  active ordering shop, so those documents are not included in application
  responses or presented as verified active-shop documents.
- `orders.OrderLiveLocation` stores the latest location for an eligible
  vehicle-pickup order, linked to its publishing Shop Admin. Coordinates are
  cleared when sharing is stopped or the order reaches a terminal status.

## 24) Order live location

The active shop endpoints `GET /api/shop/<shop_id>/` and
`GET /api/shop/nearby/` already return actual shop `latitude` and `longitude`
when available. No shop coordinates are fabricated.

### `POST /api/orders/<order_id>/live-location/`

Requires a Django access JWT for the Shop Admin who owns the order's shop.
Publishing is available only for `vehicle` pickup orders in `confirmed`,
`preparing`, or `ready` status.

```json
{
  "latitude": 19.076000,
  "longitude": 72.877700,
  "is_sharing": true
}
```

Coordinates are validated against latitude `-90..90` and longitude
`-180..180`. `is_sharing` is required. Success returns `200` with the common
live-location response shape, including JSON-number coordinates when sharing
is active.

To stop sharing:

```json
{"is_sharing": false}
```

Do not include latitude or longitude when stopping; including coordinates
returns `400`. Stopping clears the stored coordinates. Coordinates are also
cleared when the order becomes `completed`, `cancelled`, or `rejected`.

### `GET /api/orders/<order_id>/live-location/`

Requires a valid Django access JWT. Only the order's customer or owning Shop
Admin can read its location. Another customer or Shop Admin receives `404` to
avoid disclosing order existence; unsupported roles receive `403`; missing or
invalid authentication receives `401`.

The response fields are direct-root fields. Coordinates are JSON numbers while
actively sharing; both are `null` when sharing is stopped, coordinates are
missing, the order is not eligible, or the location is stale. `updated_at` is
the last location-record update time in UTC ISO-8601 format, or `null` if no
location record exists. A location older than 60 seconds is stale (exactly 60
seconds is still fresh); GET clears stale coordinates.

```json
{
  "order_id": 31,
  "is_sharing": true,
  "latitude": 19.076,
  "longitude": 72.8777,
  "updated_at": "2026-10-27T13:00:00Z"
}
```

POST is restricted to the owning Shop Admin; customers cannot publish
coordinates. Publishing requires `vehicle` pickup and an order in
`confirmed`, `preparing`, or `ready` status, otherwise POST returns `400`.
Latitude must be within `-90..90`; longitude must be within `-180..180`.
This backend endpoint supports client polling; it does not provide WebSocket
push. Flutter polling/map integration and real-device GPS validation remain
unverified in this workspace.

### Shop eligibility and existing-order restrictions

Shop Admin operations require an approved, active shop that is not suspended
and has a current verified license document. The latest verified document is
authoritative; missing documents fail closed, and pending renewal documents do
not extend an expired verified license.

Closing a shop blocks new customer discovery and orders, but does not stop
eligible Shop Admin order progression, pickup verification, or live-location
publishing for already accepted orders. Existing cart contents are retained
when a shop closes, but add/update and checkout are rejected until reopening.

Suspension or license expiry blocks ordinary Shop Admin operations. Active
orders in `accepted`, `confirmed`, `preparing`, or `ready` status appear in the
Super Admin restricted-shop order queue. The Super Admin may authorize
continuation for an individual order; the decision is audited and does not
change its order status or stock. Once authorized, the owning Shop Admin may
advance that order through the existing status endpoint, publish location, or
verify pickup as applicable. Other Shop Admin operations remain blocked.
Pending, rejected, unapproved, or missing-license shops cannot use this
per-order authorization. A renewed license restores normal operations only
after Super Admin approval in the existing application workflow.

`POST /api/orders/{order_id}/verify-pickup/` and
`POST /api/orders/{order_id}/live-location/` return `403` when the shop is
ineligible. For suspended/expired shops awaiting a decision, the response
includes `review_required: true`; pending, rejected, unapproved, or
missing-license shops are not eligible for per-order authorization. The
fixed ₹15,000 threshold, applied to `total_amount`, and Super Admin
pickup authorization
rules are unchanged.

### Order handoff OTP

Order-specific encrypted OTP storage, customer issue/read/reissue endpoints and
owning Shop Admin verification now exist in source. This is in-app vehicle
handover and does not require SMS. See
[Vehicle handover OTP](./docs/phase-2a-handover-otp.md). The payment source adds
a verified-payment gate; see [Razorpay payments](./docs/razorpay-payments.md).
Neither source contract establishes executed tests or deployment readiness.

## 25. Customer and remaining Shop Admin integration APIs

All routes below require the corresponding authenticated role. JSON endpoints
use `application/json`; file endpoints use `multipart/form-data`. List routes
use the existing pagination envelope (`count`, `next`, `previous`, `results`).

### Customer APIs

| Method | Endpoint | Request/behavior |
| --- | --- | --- |
| GET, POST | `/api/customer/verification/sessions/` | List/create a verification session. Create with multipart `document_type` and `document`; returns the session and `pending` status. |
| GET | `/api/customer/verification/sessions/{session_id}/` | Read own session status and reviewer note. |
| GET, POST | `/api/customer/support/tickets/` | List own tickets / create with JSON `subject`, `message`, and optional `category`. |
| GET | `/api/customer/support/tickets/{ticket_id}/` | Read own ticket detail and status. |
| GET, POST | `/api/customer/account/deletion-request/` | Read latest request / create a request with optional `reason`. New requests have a 30-day cancellation window. |
| POST | `/api/customer/account/deletion-request/cancel/` | Cancel the latest eligible pending request. |
| GET, PATCH | `/api/customer/notifications/preferences/` | Read/update the authenticated customer's notification preferences. |
| POST | `/api/customer/notifications/read-all/` | Mark all the customer's notifications read. |
| POST, DELETE | `/api/customer/push-token/` | Store/remove a legacy device token; push delivery is disabled. |
| GET | `/api/customer/legal/policies/` | List published legal policies only. |

Verification is reviewed manually by Super Admin; no external verification
provider or callback is integrated. Deletion requests are tracked and
cancellable, but this implementation does not yet run a scheduled account
erasure/anonymization job. Push-token registration stores legacy tokens, but push delivery is disabled.

### Shop Admin products, reports, licenses, and notifications

| Method | Endpoint | Request/behavior |
| --- | --- | --- |
| POST, DELETE | `/api/shop-admin/products/{product_id}/image/` | POST multipart `image`; DELETE removes the product image. |
| POST | `/api/shop-admin/products/bulk-import/` | Multipart CSV file under `file`; up to 2 MB / 500 data rows. Headers: `name,category,price,stock_quantity`; optional `brand,description`. Validates all rows before creating any products. |
| GET | `/api/shop-admin/reports/?range=7d` | `range` accepts `7d`, `30d`, or `90d`; returns order count, sales totals/trend, and top products for the authenticated shop. |
| GET | `/api/shop-admin/licenses/` | List legacy license files linked to a legacy Shop Admin profile. |
| GET | `/api/shop-admin/licenses/{document_id}/` | Read an owned legacy license file's metadata. |
| GET | `/api/shop-admin/licenses/{document_id}/download/` | Download an owned legacy license file. |
| GET, POST | `/api/shop-admin/application/` | Read or submit/resubmit the authenticated Shop Admin's own application, including license document metadata. |
| GET | `/api/shop-admin/application/documents/{document_id}/` | Read an owned active-application license document. |
| GET | `/api/shop-admin/application/documents/{document_id}/download/` | Download an owned active-application license document. |
| GET, PATCH | `/api/shop-admin/notifications/preferences/` | Read/update Shop Admin notification preferences. |
| POST | `/api/shop-admin/notifications/read-all/` | Mark all Shop Admin notifications read. |
| POST, DELETE | `/api/shop-admin/push-token/` | Store/remove a legacy device token; push delivery is disabled. |

The legacy `/licenses/` endpoints use the older Shop Admin profile/document
tables and may have no relationship to the active ordering shop. Prefer the
`/application/` document routes for current onboarding licenses. The product
image limit is 5 MB. CSV validation errors return `400` with a
`detail` and row-specific `errors`; an invalid file does not partially import.
The modern application-license detail and download endpoints scope documents
to the authenticated Shop Admin's shop. Push-token registration stores tokens
but does not itself send pushes; backend push delivery is disabled.

When Super Admin requests corrections, the application uses the existing
`rejected` status and is removed from the pending review queue. `GET
/api/shop-admin/application/` includes `review_history` with correction or
rejection reasons and notes. Resubmit using `POST` to the same endpoint with
`multipart/form-data`, all required onboarding fields, and a replacement
`license_document`; a successful response returns the application to `pending`
for Super Admin review. Prior review history and document records are retained.

Shop Admin password reset uses the Django account password-reset endpoints.
The existing onboarding route remains
`POST /api/shop-admin/onboarding/`.

### Super Admin APIs added for integration

| Method | Endpoint | Request/behavior |
| --- | --- | --- |
| GET, POST | `/api/super-admin/verification-sessions/` | List sessions / manually review with `session_id`, `status` (`approved` or `rejected`), and required `reviewer_note` on rejection. |
| GET | `/api/super-admin/support-tickets/` | Paginated ticket list; optional `status` filter. |
| PATCH | `/api/super-admin/support-tickets/{ticket_id}/` | Update ticket with `status` and optional `admin_response`. |
| GET, POST | `/api/super-admin/legal-policies/` | List/create policies; create with `slug`, `title`, `version`, `content`, optional `is_published`. |
| PATCH | `/api/super-admin/legal-policies/{policy_id}/` | Update policy content/version/published state. |
| GET, PATCH | `/api/super-admin/settings/` | Read fixed `high_value_order_threshold` and nearby radius; only `nearby_shop_radius_km` can be updated and updates are audited. |

Razorpay Test Mode order initialization, capture verification, webhook
deduplication and refund/reconciliation source now use a separate payment ledger.
The existing Super Admin payments list still returns `Order` summaries and does
not expose gateway transaction references. Merchant/Route approval, reviewed
migration application, isolated tests and Flutter integration remain outstanding;
live payment collection is unavailable. See
[Razorpay payments](./docs/razorpay-payments.md) for the exact new contracts.
