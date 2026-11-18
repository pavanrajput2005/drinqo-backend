# DRINQO Phase 2A: vehicle handover OTP integration

**Historical record only.** The workflow and its key settings have been removed.
Do not provision keys or implement the instructions below. See `final-auth-handover.md`.

Status: source implementation only. Migration NOT applied; Django checks and tests NOT run.
Audience: Sahil (Customer and Shop Admin Flutter integration) and backend deployment reviewer.
This feature proves customer possession of a code during physical vehicle handover. It is not home delivery, login authentication, SMS OTP, or Firebase phone OTP.

## Release prerequisites

1. Review and explicitly authorize applying `orders/migrations/0009_orderhandoverotp_handoverauditevent.py` in the intended environment. It only creates two tables and constraints; it contains no data migration or historical updates.
2. Provision independent encryption and digest keys through the deployment secret-management process, and map them to the Django settings described below. This implementation DOES NOT edit settings, read credential files, or provision keys.
3. Confirm `cryptography` is available in the deployment environment. It is present in the inspected local virtualenv; no dependency installation or runtime import check was performed for this task.
4. Review source and execute regression tests only after approval for an isolated PostgreSQL environment. Include Phase 1 regression coverage; its tests have not been run either.
5. Coordinate Customer and Shop Admin releases with backend enforcement. Old empty-body verify-pickup requests now return 400 and must never receive a proofless fallback. Apply the additive migration before serving updated code; completion signals reference the new table.
6. Configure HTTPS, body redaction in reverse proxy/APM/error reporting, and a shared production cache for consistent DRF account throttling. Database attempt/issuance limits remain authoritative independently of cache.

## Key configuration contract (not configured by this change)

The service consumes these Django settings lazily:

- `HANDOVER_OTP_ACTIVE_KEY_VERSION`: nonempty version string, at most 32 characters.
- `HANDOVER_OTP_KEYS`: mapping of version strings to dictionaries with `encryption` and `digest`.
- `encryption`: Fernet key, URL-safe base64 encoding of 32 random bytes.
- `digest`: URL-safe base64 encoding of at least 32 independent random bytes.

No actual key belongs in this document, source control, request logs, or PostgreSQL. There is no SECRET_KEY or login-OTP fallback. Missing/invalid keys or corrupted encrypted material return 503 `handover_unavailable`, without exposing configuration details. Rotation: retain previous versions while existing unexpired challenges use them; new issuance uses the active version. Removing an old version prevents display/verification of its active challenges; the customer can reissue under the new version subject to normal limits.

Fernet authenticates ciphertext. The encrypted JSON and HMAC-SHA256 digest bind order ID, challenge UUID and six-digit OTP. Comparison uses constant-time `hmac.compare_digest`. Plaintext exists briefly in backend memory and the authenticated customer's response, never as a database field. The Shop Admin receives only a challenge UUID, never the code.

## Authentication and ownership

All endpoints explicitly use project JWTAuthentication. Customer endpoints require the customer role and order ownership. Verification requires role `shop_admin` and `order.shop.owner_id == user.id`. Super Admin, customer, unrelated Shop Admin and generic Django staff accounts cannot verify. Approved staff membership is intentionally not implemented in Phase 2A.

Unknown orders and orders outside the caller's ownership scope return 404. Missing/invalid JWT returns 401; the project authentication class rejects blocked customer accounts. Account activity is governed by the existing JWT authentication implementation.

Ready status, unverified handover, high-value approval and `existing_order_can_continue()` are rechecked on issue, active read, reissue and verification. A shop closed to new orders can still fulfill an eligible existing order. Suspended/expired-license continuation requires the existing current order-specific Super Admin authorization; unrelated restrictions remain blocked.

## Customer API

Authorization: `Bearer <customer-access-token>`; POST content type: `application/json`.

### Read current state

`GET /api/orders/123/handover-otp/`

```json
{
  "order_id": 123,
  "status": "ready",
  "otp_state": "active",
  "challenge_id": "ab523d2b-992c-4588-b9f9-898c8f247cc6",
  "otp": "042731",
  "expires_at": "2026-11-10T12:10:00Z",
  "server_time": "2026-11-10T12:03:00Z",
  "attempts_remaining": 5,
  "reissue_available_at": "2026-11-10T12:01:00Z"
}
```

200 states: `not_issued`, `active`, `expired`, `locked`, `consumed`, `unavailable`.
`otp` is present as a nonnull string ONLY for an active eligible ready-order challenge. Other states return null. Challenge/timestamp fields are null before first issuance. Non-ready or already-verified orders return `unavailable`, without decrypting a code. An otherwise-ready order requiring approval or continuation review returns 403.

Reading never issues, rotates or extends a code. Times are server-provided timestamps; use `server_time` to avoid trusting device clock. Server checks are authoritative at the boundary `now >= expires_at`.

### Initial issuance / recover active response

`POST /api/orders/123/handover-otp/`

```json
{}
```

201: issued with the same response shape above. 200: existing active code returned unchanged; expiry and attempts do not reset. An expired/locked existing challenge returns 409 `reissue_required`; do not issue implicitly on GET.

### Explicit replacement

`POST /api/orders/123/handover-otp/reissue/`

```json
{"challenge_id":"ab523d2b-992c-4588-b9f9-898c8f247cc6"}
```

201: new UUID/code, ten-minute expiry and five attempts. Old challenge UUID is immediately unusable. The numeric code can coincidentally repeat; validity always includes the challenge UUID. Stale UUID returns 409 `stale_challenge`, including a duplicate request after a successful reissue. GET recovers the new code if the reissue response was lost.

Limits: at least 60 seconds between issuances; at most three issuances in the preceding rolling hour for that order. An issuance exactly one hour old no longer counts. Initial issuance counts toward the limit. Reissue resets only the new challenge's attempts, not issuance history. 429 includes `retry_after` seconds and `Retry-After` header. No automatic cancel/completion occurs on expiry, lockout or issuance limits.

## Shop Admin verification

Authorization: `Bearer <owning-shop-admin-access-token>`.
Existing URL retained: `POST /api/orders/123/verify-pickup/`.

```json
{
  "challenge_id":"ab523d2b-992c-4588-b9f9-898c8f247cc6",
  "otp":"042731"
}
```

`otp` MUST be an ASCII six-digit JSON string; leading zeros matter. Missing fields, numbers, whitespace, Unicode digits and unexpected fields return 400. Keep submitted codes out of logs. Get the current nonsecret `handover_challenge_id` from the existing Shop Admin order detail response. This additive field is also present in OrderSerializer-based customer responses; it reveals no code.

200:

```json
{
  "message":"Vehicle handover verified successfully.",
  "order_id":123,
  "status":"completed",
  "pickup_verified":true,
  "pickup_verified_at":"2026-11-10T12:04:00Z",
  "pickup_verified_by":45
}
```

422 on an incorrect code:

```json
{
  "code":"invalid_handover_otp",
  "detail":"The handover code is incorrect.",
  "attempts_remaining":4
}
```

Every well-formed wrong code for the current active challenge consumes one attempt. The fifth returns 422 with zero remaining, purges secret material and locks the challenge. Subsequent verification returns 409 `handover_otp_locked`, even with the correct code. Malformed requests and stale UUIDs do not consume challenge attempts; account throttles still apply.

Domain errors use `code` and `detail`; serializer/authentication/throttle errors use standard DRF error shapes. Important errors:

| HTTP | Code / meaning |
| --- | --- |
| 400 | Invalid request body; legacy empty-body verification rejected |
| 401 | Missing/invalid authentication |
| 403 | Role forbidden, `approval_required`, or `shop_restricted` |
| 404 | Missing order or outside ownership scope |
| 409 | `order_not_ready`, `reissue_required`, `stale_challenge`, `handover_otp_expired`, `handover_otp_locked`, `handover_otp_consumed` |
| 422 | `invalid_handover_otp` |
| 429 | `reissue_throttled`, `issuance_limit`, or standard DRF throttle |
| 503 | `handover_unavailable` |

After successful completion, repeated verification currently returns 409 `order_not_ready`; completion status is checked before challenge state. It cannot create a second completion notification.

## Transactions, abuse controls and retention

Every service operation locks shop, then order, then existing challenge inside a transaction. The order lock serializes initial challenge creation, reissue and verification, including requests with no challenge row yet. Incorrect attempts return normally from the transaction so their updates commit. Completion, consumption, notification, audit event and location-sharing shutdown commit or roll back together. No inventory changes occur in OTP operations.

Additional DRF account limits: reads 60/minute; issue/reissue 10/minute combined; verification 30/minute. These cache-backed limits are supplemental and approximate under concurrency; five-attempt and issuance limits are enforced in PostgreSQL under locks. Configure shared cache and upstream IP abuse limits during deployment; this source change does not change production infrastructure.

Ciphertext/digest are cleared on consumption, fifth failed attempt, and terminal Order.save() transitions through the existing signal. Expired material is never decrypted by these APIs; it remains encrypted until reissue/terminal transition. No background purge job or data-retention migration is included. A future retention job needs separate approval. Direct database/bulk status updates bypass Django signals and are not supported handover completion paths.

Audit events contain order, actor, challenge UUID, event type and timestamp only. They do not contain submitted OTP, ciphertext, digest, phone, vehicle number or GPS. Events are ordinary model records, not a tamper-proof external audit ledger. No request/response logging is introduced. `sensitive_post_parameters` helps Django form-error redaction. The route-specific exception reporter filter masks all traceback locals and POST metadata, including in DEBUG mode. Request/response sensitivity markers are integration hints, NOT enforcement for arbitrary middleware, proxies, APM or exception-monitoring SDKs. Exclude OTP request bodies and secret-bearing response bodies from those systems; do not serialize request._body, request.data, response.data or response.content on these routes. Configure this before rollout and verify with synthetic codes; external redaction remains UNVERIFIED.

Handled DRF responses, including handled endpoint errors, set `Cache-Control: no-store, private`, `Pragma: no-cache`, and vary on Authorization. Uncaught server errors outside DRF finalization require deployment-level no-cache/error handling. Never put an OTP in URLs, notification messages, analytics or shared serializers.

## Existing business behavior

- Phase 1 acceptance stays `confirmed`; `confirmed -> preparing -> ready` is preserved.
- Historical `accepted -> preparing` is allowed as an explicit Shop Admin action through the same existing eligibility gates, with no bulk record rewrite.
- Historical `walk_in` and missing/deleted vehicle associations retain their stored values and require the same OTP proof when ready. This does not introduce counter collection. Staff must physically identify the correct order/customer at the vehicle; OTP does not independently prove location or identity.
- New checkout requires a vehicle and vehicle ID for drive-through handover. Historical `walk_in` orders retain their stored value and are not rewritten.
- Orders totaling 15000 or more, including the 50 application fee, still require Super Admin approval. After approval, the owning Shop Admin verifies handover. Super Admin no longer performs proofless high-value completion.
- Existing order-specific restricted-shop continuation approval is still required when applicable, independently of high-value approval.
- Minimum ₹500 product subtotal, ₹50 platform fee, age verification, licensing, inventory reservation/restoration and existing customer data are unchanged.
- No new paid-payment-status gate is introduced; existing payment policy is preserved.
- Shop Admin `/complete/` remains a non-completing endpoint directing users to verification.
- Customer login OTP/provider code is not reused or changed.

## Flutter integration checklist for Sahil

Customer app: only show the code in the authenticated ready-order screen. Read state first, POST initial issuance if not_issued, show countdown from server time, and use explicit reissue for expired/locked challenges. Keep the code in memory, clear on logout/account switch/state change/expiry, hide background previews, and exclude it from persistent storage, screenshots where platform controls permit, notifications, analytics and crash reports. On a lost POST response, GET rather than immediately reissuing. Tell customers to share it only at physical vehicle handover.

Shop Admin app: display the correct order and vehicle context; refresh order detail for challenge UUID; use a six-digit string input with leading zeros; POST to verify-pickup. Do not mark completed locally before 200. On 409 refresh order/challenge state. Show remaining attempts on 422; after lockout ask the customer to reissue. On 429 honor Retry-After. Never provide a complete-anyway control. No staff account UI is part of Phase 2A.

## Validation still required

No Django commands, database operations, migrations, tests or Flutter changes were performed while implementing this source. Regression source covers encryption, leading zeros, cache headers, ownership, role denial, readiness, expiry, retry persistence, cooldown/hourly cap, stale challenge, high-value approval, shop restrictions, legacy orders, corruption/configuration failure, binding, rollback and PostgreSQL concurrent verification. Existing handover regression source is updated to require proof. Full test execution, migration-state checks, key provisioning, infrastructure redaction/rate limiting, independent security review and Flutter integration remain release prerequisites.


## Phase 2A.1 source correction pass

No migrations or models changed. No Django commands, database access or test execution occurred.

- `orders.locking.lock_order_with_shop` explicitly locks shop then order, with ownership/threshold scope rechecked after locks. OTP then locks the challenge. Super Admin approval/restricted-shop review and the overlapping Shop Admin decision path use this helper; joined-row locks are avoided.
- Issuance limits now use existing `issued`/`reissued` audit timestamps under the locked order: at most three in `(now - one hour, now]`. No new schema is needed. Counter/window fields remain bookkeeping rather than the security authority. Do NOT purge, edit or omit issuance audit events younger than one hour: the rolling limiter depends on their completeness. Event insertion failure rolls back issuance.
- GET retains exclusive shop/order/challenge locks. Removing them without another consistency protocol could race completion, restrictions or reissue and expose a stale secret. The tradeoff is per-shop polling contention; a future optimization requires separately reviewed consistency semantics and PostgreSQL validation.
- OrderSerializer querysets preload `handover_otp` where applicable; response fields and pagination behavior are unchanged.
- The Unicode fixture uses explicit escapes; production validation still requires ASCII `[0-9]{6}`.
- Added test source covers real JWTs, invalid JWTs, blocked/inactive accounts, key failures/rotation, exact boundaries, rolling-hour bursts, cross-client account throttling, concurrent issue/reissue, approval/review/verification races, committed HTTP 422 counters, audit failure rollback, terminal secret clearing, privacy/no-cache behavior, unauthorized OTP access, proofless complete rejection and shop ownership/restriction changes while verification waits.

These tests are NOT EXECUTED. The cache-backed test uses two clients in one process; it does not prove multiworker behavior. Shared production cache, worker configuration, upstream limits and proxy/APM redaction remain deployment prerequisites. Cryptography loading, migration execution, PostgreSQL concurrency, Flutter integration and production readiness remain UNVERIFIED.
