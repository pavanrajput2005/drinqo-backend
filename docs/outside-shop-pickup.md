# Walk-in and Vehicle outside-shop handover

> Historical contract note: customer handover OTP issuance and verification
> are retired. Current completion uses staff-confirmed handover; legacy routes
> remain only as HTTP 410 compatibility tombstones.

Both modes mean shop staff comes outside the licensed shop to the customer within 100 metres. Walk-in customers wait on foot; Vehicle customers wait in or near their selected vehicle. Neither mode is home delivery, third-party delivery or collection inside the shop.

## Checkout contracts

Use the existing customer JWT and `Content-Type: application/json` for both:

- `POST /api/customer/checkout/preview/`
- `POST /api/orders/create/`

Walk-in:

```json
{"pickup_mode":"walk_in","latitude":19.123456,"longitude":72.123456}
```

Vehicle:

```json
{"pickup_mode":"vehicle","vehicle_id":8,"latitude":19.123456,"longitude":72.123456}
```

Coordinates are examples. Send the customer's current foreground GPS location, not these literal values. Latitude/longitude are required finite coordinates in geographic range, with at most six decimal places. Location permission alone is insufficient. The backend calculates distance from the server-derived ordering shop and requires <=100m for both modes. Missing shop coordinates block checkout. Preview retains its existing contract: below-minimum or outside-radius conditions return HTTP 200 with `can_checkout:false` and a detail; creation rejects these conditions with HTTP 400.

Walk-in does not require a saved vehicle and persists a null vehicle reference. Including `vehicle_id` on a Walk-in request returns 400 with `errors.vehicle_id`, rather than silently changing the chosen mode. Vehicle requires an existing vehicle belonging to the current customer. An omitted pickup_mode retains the existing default of vehicle, including its vehicle requirement; Flutter must send the user's explicit choice.

The shop, products, current prices and quantities come from the customer's server-side cart. Checkout ignores caller-supplied shop/pricing/payment fields. Existing Personal Details and age eligibility remain required. Minimum products subtotal is INR 500. The INR 50 application/platform fee is one charge; `platform_fee` is an alias of `application_fee`. Total >= INR 15,000 including the fee requires Super Admin approval. These rules, cart locking, atomic inventory deduction and rollback are unchanged.

Creation returns HTTP 201 with the existing bare Order object: use `id` for later URLs. Both customer and Shop Admin responses already include `pickup_mode`, `vehicle` and `vehicle_details`. Walk-in has null vehicle/vehicle_details; Vehicle retains the selected vehicle details. No schema migration is needed: Order already stores both modes.

## Approval, payment and staff workflow

The existing state flow is unchanged:

`pending` (or `pending_super_admin -> admin_approved`) -> `confirmed` -> `preparing` -> `ready` -> `completed`.

Owning Shop Admin accepts using `/api/shop-admin/orders/{id}/decision/` with `{"decision":"accepted"}`. High-value orders must be approved first. Verified captured payment is required before preparation, readiness and handover. Gateway configuration has not changed; the current disabled gateway remains a live-testing blocker. Do not simulate paid status in an application client.

Shop Admin must display pickup_mode, render vehicle details only for Vehicle, and instruct staff to meet the customer outside. Walk-in acceptance notifications now say to wait outside within 100 metres, rather than referring to a vehicle. Ready notifications remind both modes to wait outside for staff. Message text is display copy; integrations should branch on fields and error codes, not exact English strings.

## Foreground GPS at OTP issuance: client update required

After readiness and verified payment, obtain fresh foreground customer coordinates each time the customer requests or replaces a code. No continuous or background tracking is required. OTP reading and staff verification do not publish or accept location.

Issue (or reuse an active challenge):

`POST /api/orders/{id}/handover-otp/`

```json
{"latitude":19.123456,"longitude":72.123456}
```

Reissue:

`POST /api/orders/{id}/handover-otp/reissue/`

```json
{"challenge_id":"11111111-1111-4111-8111-111111111111","latitude":19.123456,"longitude":72.123456}
```

This is an intentional request-contract change for both modes: old empty issue bodies and reissue bodies containing only challenge_id now return HTTP 400 field-validation errors. Unknown fields remain rejected. A customer without foreground GPS cannot issue/reissue a code. Both actions validate reported location against the shop's current coordinates under the existing shop/order locks, including repeated POSTs for an already active code. GET does not issue, replace, or extend a code.

`GET /api/orders/{id}/handover-otp/` retains the existing Challenge response fields: `order_id,status,otp_state,challenge_id,otp,expires_at,server_time,attempts_remaining,reissue_available_at`. Issue returns 201 for a new challenge or 200 when reusing an active challenge; reissue returns 201. The challenge response never includes customer coordinates. No location history, background tracking or new persistence fields have been added.

New location errors (HTTP 409):

```json
{"code":"outside_handover_radius","detail":"You must be within 100 meters of the shop to request a handover code.","pickup_radius_m":100}
```

```json
{"code":"shop_location_unavailable","detail":"Shop location is unavailable; handover cannot be validated."}
```

Missing/malformed/nonfinite/out-of-range location values return HTTP 400 field errors. Failure to validate location does not rotate/consume a challenge or use a verification attempt. Existing request throttles still apply. Reissuance continues to require the current challenge ID, cooldown and rolling-hour issuance limit.

Staff verifies using the unchanged endpoint and payload:

`POST /api/orders/{id}/verify-pickup/`

```json
{"challenge_id":"11111111-1111-4111-8111-111111111111","otp":"012345"}
```

Only the owning Shop Admin may verify. Successful verification returns the existing fields `message,order_id,status,pickup_verified,pickup_verified_at,pickup_verified_by` and atomically completes the order. The success message identifies Walk-in or Vehicle. OTP remains a six-character string so leading zeroes survive. Invalid, expired, reused, cross-order and cross-shop codes are rejected. Existing encryption/digest binding, attempt limits, throttles, audit entries, secret cleanup, payment/approval/shop restrictions and atomic completion remain in force. `/complete/` still refuses completion without pickup verification; no weaker completion route exists.

## Location assurance and remaining limits

The server enforces the 100m check on reported coordinates at checkout and again at code issuance/reissuance. The available live-location API is Shop Admin-published and is not evidence of customer GPS. It remains vehicle-specific; this change does not introduce Walk-in live tracking or allow customers to publish there.

Client-reported coordinates do not prove physical presence, a foreground measurement's freshness, or whether someone is inside versus outside a building. The backend does not claim GPS spoofing prevention. No polygon/building boundary or trusted proximity sensor exists in this workflow. A customer could also move during the code's existing 10-minute lifetime. GET/read and final OTP verification do not perform a new GPS measurement. Staff must physically confirm the customer is outside the shop within 100m before accepting the OTP. An OTP proves knowledge of a challenge, not location. Stronger final-moment proximity assurance would require a separately designed mechanism and client contract; it has not been fabricated from shop-published location data.

## Validation and deployment

Tests cover both checkout modes, ownership and geofences, financial thresholds, payment-gated progression, OTP security/location checks and PostgreSQL stock races. See `orders/test_pickup_modes.py` and additions to `orders/test_inventory_concurrency.py`. Existing OTP/payment fixtures now explicitly supply synthetic foreground coordinates to the same production API/service; cryptographic and state checks are not bypassed.

Run regression tests only with `config.test_settings` against a verified disposable PostgreSQL cluster; never the shared development database. No migration application, production data update or gateway activation is part of this change. The earlier `BACKEND_ORDER_PLACEMENT_AUDIT.md` remains the historical pre-change audit; this document describes the updated contract. Unrelated audit findings such as inventory admin writers, DOB synchronization, payment projection and refund-review recovery are not changed here.

### Results from this implementation

- `.\venv\Scripts\python.exe -B manage.py check`: passed, no system-check issues. The existing short development JWT signing-key warning remains.
- `.\venv\Scripts\python.exe -B manage.py makemigrations --check --dry-run`: passed, no changes detected. No migrations were applied to the shared database.
- Relevant regression run: **193 tests, 192 passed, 1 existing error**, no skips. All 12 new pickup-mode tests and three new stock-race/rollback tests passed. Existing OTP encryption, expiry, replay, ownership, throttling, retention and concurrency tests passed; verified-payment progression and capture/refund concurrency tests passed.
- The run used a freshly initialized PostgreSQL 18 cluster in a unique temporary directory, SCRAM credentials, a loopback-only ephemeral port, and distinct connection/test databases. Its actual PostgreSQL data_directory was checked before creating the connection database or running Django. `config.test_settings` disabled dotenv and used test-only routing; the existing isolated test runner blocked requests-based provider HTTP and isolated media. The final run used MD5PasswordHasher only in the test child process to avoid unrelated password-hashing costs; no project password settings were edited. The temporary cluster was stopped after the run.
- The remaining error is `orders.test_payments.PaymentApiTests.test_signed_failed_payment_webhook_is_idempotent_and_never_marks_paid`: `orders/payments.py:299` calls `_payment(entity)` without defining/importing `_payment`, causing NameError. That payment application module was not changed by this work. This is a broader payment-integration blocker, not a failed pickup-mode/GPS assertion; the regression remains visible and was not skipped or weakened.

Latest full regression log: `C:\Users\Pavan Rajput\AppData\Local\Temp\drinqo-pickup-tests-e0rrtk74\tests.log`. Provider test fixtures were updated with complete saved Personal Details because the current shared checkout eligibility policy requires them; cryptographic/financial assertions remain unchanged. Real device GPS, outside-shop staff operations, and provider-backed test payments still require coordinated Flutter/Shop Admin testing. The production payment gateway stays disabled.

### Exact project files changed

| File | Change |
|---|---|
| `customers/serializers.py` | Accept both pickup choices; reject a Walk-in vehicle conflict; preserve vehicle/default validation |
| `orders/serializers.py` | Required foreground coordinates on OTP issue/reissue; keep staff verification limited to challenge_id and OTP |
| `orders/handover_views.py` | Forward validated issuance coordinates; mode-neutral owning-shop permission text |
| `orders/handover_otp.py` | Server-side issuance/reissuance geofence; mode-aware completion copy; existing secure lifecycle retained |
| `shop_admin/api_views.py` | Walk-in acceptance copy and outside-shop ready notification |
| `customers/test_customer_security_fixes.py` | Replace obsolete vehicle-only expectation with both-mode contract expectation |
| `orders/test_pickup_modes.py` | New checkout, financial, readiness, OTP, location and rollback regression coverage |
| `orders/test_inventory_concurrency.py` | Mixed-mode stock race, Walk-in double-click/rollback; explicit eligible phone/location fixture |
| `orders/testing_handover.py` | Shared synthetic location for test-only handover helper |
| `orders/test_handover_otp.py` | Explicit shop/foreground coordinates for existing OTP security tests |
| `orders/test_handover_corrections.py` | Supply required foreground coordinates without weakening existing security assertions |
| `orders/test_handover_retention.py` | Supply coordinates to existing retention/reissue service tests |
| `orders/test_payments.py` | Eligible saved-profile and location fixtures; OTP requests send GPS |
| `orders/tests.py` | Explicit shop location for existing handover fixture |
| `shop_admin/tests.py` | Make Vehicle message fixtures explicitly Vehicle rather than relying on model Walk-in default |
| `docs/outside-shop-pickup.md` | Updated Flutter/Shop Admin contracts, operational limits, validation and file inventory |
