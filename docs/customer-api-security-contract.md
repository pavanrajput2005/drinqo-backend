# Customer API security contract

## Verified phone login and linking

`POST /api/accounts/otp/verify/` accepts `phone` and `otp`. It authenticates only
the canonical, OTP-proven phone identity. Historical `User.phone` and customer
profile contact fields are never used to select an account for this endpoint.
If no canonical identity exists, a new passwordless customer account is created;
legacy contact-phone records are not merged automatically.

Authenticated customers change their login phone in two steps:

1. `POST /api/accounts/phone-link/request/` with `{ "phone": "+919876543210" }`.
2. `POST /api/accounts/phone-link/verify/` with the same `phone` and `{ "otp": "123456" }`.

The second endpoint returns `409` when the phone belongs to another account. A
successful response contains `phone_verified` and `verified_phone_last4`.
The legacy `phone` response field remains a contact field and must not be treated
as verified. `phone_verified` and `verified_phone_last4` are available on account
and customer profiles.

## Drive-through checkout

New checkout preview and order creation require `pickup_mode: "vehicle"` and an
owned `vehicle_id`; `walk_in` is not accepted for a new order. Historical orders
retain their existing pickup mode. Product subtotal minimum is ₹500 and the
separate platform fee remains ₹50.

## Favorites and device tokens

`POST /api/customer/favorites/shops/` accepts `{ "shop_id": 123 }`. The shop must
be eligible to accept new orders. Repeating the same request returns `200` with
the existing favorite; a new favorite returns `201`.

`POST /api/customer/push-token/` and `POST /api/shop-admin/push-token/` accept
`{ "token": "...", "platform": "android|ios|web" }`. Repeating registration by
the same authenticated user is idempotent (`200`); registering a token held by a
different user returns `403`. Registration stores a token only—it does not claim
that a push message was delivered.
