# DRINQO — verified backend and final Flutter integration handoff

Verification date: 17 November 2026, Asia/Calcutta. Project: `D:\drinqo_backend`.

## 1. Verification result and scope

The implementation report was checked against current URL resolution, models,
views, serializers, provider verifiers, JWT configuration, migrations and fresh
isolated PostgreSQL tests. This document supersedes the earlier report's claim
that two migrations still need applying to the configured database.

| Claim | Current evidence |
|---|---|
| Phone/SMS OTP retired | Active routes resolve to RetiredPhoneOTPView; regression tests assert 410 and no JWT/provider call |
| Handover OTP retired | Active routes resolve to RetiredHandoverOTPView; services also reject with 410 |
| Google/Apple login | Source and mocked/synthetic cryptographic tests verified; no live provider authorization performed |
| Secure email OTP | HMAC-SHA256 digest storage, purpose/user binding, five-minute expiry, one-use consumption and durable limits verified |
| Password/JWT sessions | Registration/login/me/refresh/logout/reset tested; access/refresh remain Django JWTs |
| Staff handover | Owning Shop Admin only; readiness, payment, approval, shop eligibility, reported location, acknowledgment and atomic replay behavior checked |
| Original 118 tests | Re-run in a fresh PostgreSQL cluster: 118 passed, 0 failed, 0 errors, 0 skipped |
| Two unapplied migrations | Outdated: both are already applied in the configured database, with the required schema present |

Runtime URL resolution confirmed the exact routes below, rather than merely
searching for endpoint strings. No Flutter files, credentials, dependency versions
or payment mode were changed. Authentication success contracts and existing Order
fields were retained; the necessary additive customer_id Order field is described
below. No new endpoints were introduced by this verification.

Two malformed-input defects were found by additional contract tests and fixed
locally in accounts/views.py: a JSON array passed to logout previously raised
AttributeError, and a base64-decodable nonnumeric reset uid raised an ORM
ValueError. Both now return existing-format 400 errors. Valid requests and success
responses are unchanged. Regression cases also cover nonpositive/out-of-range uid.

An additional integration claim was corrected: customer_id existed on the
ShopAdminOrderSerializer, but actual Shop list/detail routes use OrderSerializer.
The active serializer now exposes customer_id as an additive read-only field;
Flutter can build the required handover payload from an actual routed response.
Existing fields/routes are preserved. The same additive field is visible on
Customer order responses, which are already scoped to the owning account.

Files changed in this verification pass:

- `accounts/views.py`: the two narrow validation guards.
- `accounts/test_flutter_contracts.py`: seven exact-contract/error regression tests.
- `orders/serializers.py`: expose read-only customer_id on the actively used DTO.
- `orders/test_flutter_handover_contracts.py`: routed DTO/completion/error checks.
- `docs/final-flutter-integration-handoff.md`: this report.

## 2. Database verification and migration results

The configured host is loopback and the settings default to development, but
neither fact proves exclusive/non-shared ownership. The configured database was
accessed only with read-only transaction settings. No migration or application
write was performed against it in this verification pass.

| Migration | Dependency | Actual configured DB result |
|---|---|---|
| `accounts.0012_secure_email_otp` | `accounts.0011_mobile_auth` | Already applied; otp_code storage and purpose/requesting_user columns present |
| `orders.0013_staff_confirmed_handover` | `orders.0012_rename_orders_pay_kind_status_idx_orders_paym_kind_5be486_idx` and swappable User dependency | Already applied; both new handover tables/columns present |

The User fields USERNAME_FIELD="email" and user_type are correctly declared on
User, not EmailAuthState. The configured accounts_user table contains user_type
and all mobile-auth fields; accounts_emailauthstate has only throttling fields.
No User reconstruction, role conversion or applied-migration rewrite is needed.

Migration review: accounts.0012 alters only transient EmailOTP storage, adds its
binding fields and clears old plaintext codes. It does not delete users, roles,
passwords, provider links, profiles, orders or phone identities. Orders.0013 creates
CustomerHandoverLocation and StaffHandoverConfirmation; it does not drop old
OrderHandoverOTP/HandoverAuditEvent data or change prices/inventory. No claim is
made about who previously applied these migrations or their historical timing.

Executed against configured settings with PostgreSQL read-only options:

| Command | Exit | Result |
|---|---:|---|
| `python manage.py showmigrations` | 0 | All listed migrations marked [X], including both above |
| `python manage.py migrate --plan` | 0 | No planned migration operations |
| `python manage.py check` | 0 | No issues, 0 silenced |
| `python manage.py makemigrations --check --dry-run` | 0 | No changes detected |

All four emitted the existing warning that the development JWT fallback signing
key is shorter than 32 bytes. This is a deployment configuration blocker, not a
reason to invent/print a secret. There is no pending schema operation to reapply
on this target. If another deployment has pending migrations, inspect its identity,
backup and ownership and obtain authorization before `migrate`; production/shared
targets are not authorized merely by this document.

Fresh isolated test databases were initialized and migrated by the reviewed
`tools/run_auth_handover_tests.py` runner, using new clusters, roles and DB names.
Those are the only databases written during testing. Tests use fake mail/cache
and blocked/mocked provider HTTP; their own servers are stopped and evidence is
retained. No shared PostgreSQL service or database was stopped/dropped.

## 3. General HTTP and Flutter parsing contract

Use the configured backend origin followed by the exact paths below, including
trailing slash. Send `Content-Type: application/json`. Protected APIs require:

```http
Authorization: Bearer <django_access_jwt>
```

All JSON values below are examples/placeholders; no real token, OTP or credential
is included. HTTP status is part of the contract. Parse errors structurally;
messages and throttle wait times can vary. Do not infer success from a nonempty
body, SDK callback or HTTP 200 on an OTP-send/reset-request endpoint.

### Shared new-login success object: HTTP 200

Google, Apple and successful email-OTP verification return exactly these keys:

```json
{
  "access": "<django_access_jwt>",
  "refresh": "<django_refresh_jwt>",
  "is_profile_complete": false,
  "user": {
    "id": 123,
    "email": "customer@example.com",
    "first_name": "",
    "last_name": "",
    "birth_date": null,
    "mobile_number": null,
    "auth_provider": "google",
    "is_profile_complete": false,
    "user_type": "customer"
  }
}
```

Values reflect the stored account. A new Google/Apple/email account uses provider
google/apple/email respectively; an existing customer's original signup provider
is retained. DOB is YYYY-MM-DD or null; mobile_number is a string or null.

Important role distinction: Google/Apple and password login can authenticate an
already-linked eligible Customer, Shop Admin or Super Admin. The backend preserves
the role, never promotes based on the provider. The Customer Flutter application
must require returned user_type="customer" before entering its shell, including
after restore/refresh. Email OTP is customer-only and rejects existing admin roles.
Protected Customer APIs enforce CustomerOnly independently of UI checks.

is_profile_complete means the new DOB/mobile form has been completed. It is not
age_verified, not full address readiness and not checkout permission. Legacy users
may have usable profile data while this newer flag remains false. Keep existing
remote profile and 21+ declaration checks. Profile changes never grant age proof.

### Error families

New `/api/auth/` and `/api/user/profile/` errors use:

```json
{"error": "<safe error message>"}
```

Examples are specified per endpoint below. The older `/api/accounts/` APIs keep
DRF `detail`, `code` and field errors; they do not adopt the new error wrapper.
Order errors likewise retain `code`/`detail` or field-array structures. Flutter
should handle error -> detail -> field/non_field_errors, including lists.

## 4. Authentication API contracts

### 4.1 Google: POST /api/auth/google/

Public endpoint; no existing JWT required. Request:

```json
{"id_token": "<Google ID token>"}
```

Required string, max 8192 characters; unknown fields rejected. HTTP 200 returns
the shared login object. It verifies signature/issuer/expiry/subject, configured
audiences and authorized party, and verified email before new-account creation.
Existing accounts resolve by linked Google subject, never matching email alone.

Exact representative errors:

```json
{"error": "id_token: This field is required."}
```

400 for missing/invalid fields or malformed JSON; 401 for rejected proof:

```json
{"error": "Invalid or expired Google token."}
```

401 on an unlinked existing-email collision:

```json
{"error": "Sign in with your existing method and link this provider first."}
```

503 for missing/invalid audience configuration or verifier infrastructure failure:

```json
{"error": "Google verification is temporarily unavailable."}
```

Ineligible accounts get 401; rate limits 429; storage errors 503. Flutter sends an
ID token, keeps it out of logs/persistent storage, and stores only returned Django
session tokens. Match actual platform/server client IDs to the backend allowlist.
The inspected Google IDs are present and syntactically valid; live compatibility
with the Flutter build/provider console has not been demonstrated.

### 4.2 Apple: POST /api/auth/apple/

Public. Request:

```json
{"identity_token": "<Apple identity token>", "first_name": "Example", "last_name": "Customer"}
```

Only identity_token is required (string, max 8192). Names are optional strings,
max 150 each. HTTP 200 returns the shared login object. Apple RS256 signature,
official JWKS, issuer/audience, expiry/iat/subject and required claims are checked.
First creation needs verified email; existing linked sub can be reused without
another email claim. Private-relay addresses are supported. No email-only merge.

400 example:

```json
{"error": "identity_token: This field is required."}
```

401:

```json
{"error": "Invalid or expired Apple token."}
```

503:

```json
{"error": "Apple verification is temporarily unavailable."}
```

Missing first-login email returns 401 with "A verified email is required on first
sign-in." Collision/ineligible/rate/storage errors follow the Google categories.
APPLE_CLIENT_IDS is currently absent, so local valid-shaped requests fail closed
with 503. Configure real Apple bundle/Services audiences and platform capability,
domain/return URLs as applicable; no credentials or Apple-link endpoint are invented.
Flutter must handle first/repeat authorization and retain the Django account ID.
Omit absent optional name fields on repeat authorization instead of sending null;
the optional fields accept strings, not null.

### 4.3 Email OTP send: POST /api/auth/send-otp/

Public. Required request:

```json
{"email": "customer@example.com"}
```

HTTP 200 exactly:

```json
{"message": "OTP sent successfully"}
```

No JWT is issued here. No private-account existence check is disclosed. Backend
normalizes the email, generates six numeric digits using secrets, stores a keyed
digest only and sends email. A successful response means the configured mail
backend accepted it; console mail does not establish real delivery.

400 example:

```json
{"error": "email: This field is required."}
```

429 per-address cooldown:

```json
{"error": "Wait 60 seconds before requesting another OTP."}
```

429 per-address send budget:

```json
{"error": "Too many OTP requests. Try again later."}
```

503 delivery failure:

```json
{"error": "Unable to send OTP. Please try again later."}
```

Five sends/hour/address and 60-second cooldown are durable. Public IP throttling
also applies (configured five/hour). Flutter shows inbox guidance/cooldown, never
extracts an OTP from HTTP, and does not manufacture a session after this response.

### 4.4 Email OTP verify: POST /api/auth/verify-otp/

Public. Both fields required as strings:

```json
{"email": "customer@example.com", "otp": "012345"}
```

OTP is exactly six ASCII digits, preserving leading zeros. HTTP 200 returns the
shared login object and both Django JWTs. It consumes the valid purpose=login
code once, preserves an existing eligible customer or creates a new customer with
an unusable password. Link-email proofs cannot be used here.

400 wrong/expired/used code:

```json
{"error": "Invalid or expired OTP."}
```

401 blocked/inactive/admin account:

```json
{"error": "Unable to sign in with this account."}
```

429 durable attempt limit:

```json
{"error": "Too many verification attempts. Try again later."}
```

Codes expire at five minutes. Ten verifications/hour/address survive resend and
consumption; public IP scope is configured at twenty/hour. Field/malformed input
is 400, storage failure 503. Flutter sends the code as text, clears it after use
and uses only the returned JWTs. Real inbox delivery remains unverified.

### 4.5 Email/password login: POST /api/accounts/login/

Public. Required request (email is the actual User.USERNAME_FIELD):

```json
{"email": "customer@example.com", "password": "<password>"}
```

HTTP 200 exactly:

```json
{"refresh": "<django_refresh_jwt>", "access": "<django_access_jwt>"}
```

It does not return nested user or profile-completion flags. Fetch /me afterwards.
Flutter should trim/lowercase email, not password. Do not substitute username.
Missing fields return 400 field arrays, for example:

```json
{"email": ["This field is required."], "password": ["This field is required."]}
```

Wrong credentials/inactive account return 401:

```json
{"detail": "No active account found with the given credentials"}
```

Blocked roles return 403 (legacy wording retained):

```json
{"detail": "This customer account is blocked."}
```

Throttling 429 and cache-unavailable 503 apply. Existing admin roles remain valid
for their own apps; Customer Flutter must enforce the returned /me role.

### 4.6 Refresh: POST /api/accounts/token/refresh/

No Bearer access is required; refresh JWT is required in JSON:

```json
{"refresh": "<django_refresh_jwt>"}
```

HTTP 200 exactly:

```json
{"access": "<new_django_access_jwt>"}
```

Refresh rotation is currently off: retain the existing refresh token. Access
lifetime is five minutes; refresh one day. Missing refresh is 400 field-array;
invalid/expired/blacklisted JWT is 401 with DRF detail/code, e.g.:

```json
{"detail": "Token is blacklisted", "code": "token_not_valid"}
```

Deleted/inactive user gives 401; blocked user 403 with the legacy message above.
Use the existing single-flight refresh/retry logic and account/session revision
checks. On failed refresh clear the session; do not loop/retry an ambiguous order
creation just because auth transport refreshed.

### 4.7 Logout: POST /api/accounts/logout/

Bearer Django access is required; request:

```json
{"refresh": "<the same user's refresh JWT>"}
```

Success is **205 with an empty body**, not JSON. It blacklists that refresh token.
Existing access JWTs remain valid until expiry. Missing/invalid access gives 401;
blocked access 403. A different user's refresh gives 403:

```json
{"detail": "Refresh token does not belong to this user."}
```

400 missing refresh or non-object JSON:

```json
{"refresh": "This field is required."}
```

400 invalid/expired refresh:

```json
{"refresh": "Invalid or expired refresh token."}
```

Flutter must not JSON-decode a successful empty logout response; clear local
tokens/account-bound caches and cancel in-flight account work.

### 4.8 Current user: GET /api/accounts/me/

Bearer access required. No request JSON. HTTP 200 shape:

```json
{
  "id": 123, "firebase_uid": null, "email": "customer@example.com",
  "phone": "9876543210", "user_type": "customer", "is_verified": false,
  "age_verified": false, "age_self_declared": false,
  "age_self_declared_at": null, "date_of_birth": null, "age_verified_at": null
}
```

Dates/timestamps are strings when present. Missing auth 401:

```json
{"detail": "Authentication credentials were not provided."}
```

Invalid access gives 401 detail/code; blocked access 403:

```json
{"detail": "This account is blocked."}
```

This response is flat and has no is_profile_complete/auth_provider field. Do not
invent a GET /api/user/profile/ to restore those fields; use existing remote
Customer profile validation and retain the original session helpers.

### 4.9 Registration: POST /api/accounts/register/

Public. Required fields are username, email, password; phone is optional:

```json
{"username": "customer123", "email": "customer@example.com", "phone": "9876543210", "password": "<strong password>"}
```

HTTP 201 exactly:

```json
{"username": "customer123", "email": "customer@example.com", "phone": "9876543210"}
```

Omitted phone yields an empty phone string. No ID/password/JWT is returned.
New role is the model's customer default; callers cannot assign an admin role.
Missing fields/invalid email/weak password/duplicate identity return 400 field
errors. Case-insensitive duplicate-email example:

```json
{"email": ["An account with this email already exists."]}
```

A simultaneous DB uniqueness collision returns 400 detail; no account merge is
performed. Flutter should proceed to an actual login flow after registration.

### 4.10 Forgot password: POST /api/accounts/password-reset/

Public. Required request:

```json
{"email": "customer@example.com"}
```

HTTP 200 exactly:

```json
{"detail": "If an account with that email is eligible, password reset instructions will be sent."}
```

Missing/invalid email is 400 field errors; rate limit 429. A 200 is deliberately
generic for unknown/ineligible accounts. **PASSWORD_RESET_URL is currently unset:
the view returns this 200 without sending reset mail.** Real recovery requires
that URL, sender and delivery configuration. Passwordless Google/OTP accounts are
excluded by the existing Django reset form; they use their provider/email OTP and
authenticated set-password flow instead. Flutter must not show account existence
or delivery certainty based on this body.

### 4.11 Reset password: POST /api/accounts/password-reset/confirm/

Public; reset link proof authorizes the action, not a login JWT. Required:

```json
{"uid": "<base64 user ID>", "token": "<reset token>", "password": "<new strong password>", "confirm_password": "<same password>"}
```

HTTP 200 exactly:

```json
{"detail": "Password reset successfully."}
```

400 invalid/expired link, invalid/nonpositive/out-of-range decoded UID or user:

```json
{"detail": "Invalid or expired password reset link."}
```

Missing uid/token is 400 field arrays; weak/mismatched passwords are 400 field
errors, e.g.:

```json
{"confirm_password": ["Passwords do not match."]}
```

Rate limits 429. No JWTs are returned; outstanding refresh tokens are blacklisted.
Flutter removes reset credentials from routes/history and asks the user to sign
in again after success. Preserve the existing public/deep-link reset screens.

### 4.12 Existing supporting endpoints — do not duplicate them

- POST /api/accounts/google-login/ accepts id_token, returns 200 flat
  id/email/phone/user_type/is_verified/age_verified/access/refresh. It uses the same
  verifier as /api/auth/google/, but has the old DRF error format.
- POST /api/accounts/google-link/ uses Bearer access + id_token and the existing
  explicit linking rules. Never silently link by email.
- PATCH /api/user/profile/ uses Bearer access and
  `{"birth_date":"1990-01-01","mobile_number":"9876543210"}`. Success 200 is the
  shared login object's user portion only. Both values must be valid/present
  overall (PATCH can omit one already valid). DOB cannot be future; phone is
  10–15 ASCII digits. Verified DOB changes are restricted; changed DOB resets
  age declaration. Editable contact data never certifies a new phone identity.
- POST /api/accounts/set-password/ uses Bearer access plus password and
  confirm_password (current_password also required if already usable). It needs
  verified non-placeholder email, returns 200
  `{"detail":"Password set successfully. Sign in again."}` and revokes refreshes.
- Authenticated legacy phone-only accounts can use existing
  /api/auth/link-email/send-otp/ and /api/auth/link-email/verify-otp/ with email,
  then email+otp. The send returns the normal email-send message; verify returns
  the shared login object, keeping the same User ID/profile/orders. No live
  session or other linked proof means reviewed manual support recovery; do not
  recreate/merge an account using a matching contact number.

## 5. OTP retirement verification

These exact POST routes resolve to retirement views and return 410; no body
fields can cause a login/provider call/order completion:

| Paths | Response |
|---|---|
| /api/accounts/otp/send/, /api/accounts/otp/verify/, /api/accounts/phone-link/request/, /api/accounts/phone-link/verify/, /api/accounts/firebase-login/ | phone_otp_retired below |
| /api/orders/{id}/handover-otp/, /api/orders/{id}/handover-otp/reissue/, /api/orders/{id}/verify-pickup/, /api/shop-admin/orders/{id}/complete/ | handover_otp_retired below |

Legacy handover GET requests handled by the tombstone also return 410.

```json
{"code":"phone_otp_retired","error":"Phone/SMS OTP is retired. Use email/password, email OTP, Google or Apple. Existing signed-in users can link a verified email; otherwise contact support for account recovery."}
```

```json
{"code":"handover_otp_retired","detail":"Handover OTP is retired. The owning Shop Admin must explicitly confirm outside-shop handover."}
```

The MSG91 provider and handover OTP services have been removed. Historical
phone/Firebase identities, contact data and order-OTP audit/models remain.
Only the HTTP compatibility routes return 410. Order handover_challenge_id is always
null. Email OTP continues to be supported independently of this retirement.

## 6. Final staff handover contract

POST /api/shop-admin/orders/{id}/confirm-handover/

Bearer Django access token of the active, unblocked **owning shop_admin** is
required. No delegated staff identity/table exists in this implementation;
arbitrary employees, customers and Super Admins do not gain this authority.
The assigned owning Shop Admin is the authorized staff identity for this API.
GET /api/shop-admin/orders/ currently returns a bare array of OrderSerializer
objects; GET /api/shop-admin/orders/{id}/ returns one such object. Both now expose
customer_id. Preserve that ID when opening the handover dialog. These DTOs do not
contain an invented can_confirm_handover flag or the full captured-payment ledger.
Use fresh server-filtered ready/paid order data as UI preconditions and always
let the confirmation POST perform the authoritative, current eligibility check.

Required exact request:

```json
{
  "customer_id":123,
  "customer_checked":true,
  "order_checked":true,
  "physically_handed_over":true,
  "outside_shop_confirmed":true,
  "latitude":"18.520430",
  "longitude":"73.856744",
  "location_captured_at":"2026-11-17T09:00:00Z"
}
```

Coordinates/time are illustrative; send a real fresh foreground staff fix. Every
acknowledgment must be JSON true (strings/numbers rejected). customer_id must
match the backend order; Shop order responses expose it. Unknown fields, including
status or otp, are rejected. No passive GET can complete an order.

Success HTTP 200:

```json
{
  "order_id":456,"status":"completed","pickup_mode":"walk_in",
  "pickup_verified":true,"pickup_verified_at":"2026-11-17T09:00:01Z",
  "pickup_verified_by":42,"handover_method":"staff_confirmation",
  "confirmation_id":9,"already_completed":false
}
```

Vehicle uses pickup_mode="vehicle". A safe authenticated owning-shop replay returns
the original confirmation/actor/time with already_completed=true, with no new
audit, notification or stock deduction. Historic completed orders without that
new confirmation are not retroactively certified by replay.

Server validation is authoritative:

- Order belongs to the authenticated owner's shop, with scope rechecked after
  shop/order row locks. Wrong role 403, foreign/missing order 404, missing JWT 401.
- First completion requires ready and pickup_verified=false. Pending, preparing,
  rejected, cancelled and unsupported historic terminal states cannot complete.
- Captured payment must be verified against OrderPayment: paid order flag,
  captured ledger status/time, nonempty provider IDs, matching total/subtotal/fee
  split and zero refunded amount. A UI/local/manual paid flag is insufficient.
- Total >=15000 including fee requires recorded Super Admin approval timestamp
  and an approving actor with super_admin role.
- Shop licensing/activation/restriction rules are evaluated through the existing
  existing_order_can_continue policy. Suspended/expired eligible existing orders
  need the specific current-state continuation review; other restrictions block.
- Approved shop coordinates are required. Staff-reported fix must be within
  100 metres and at most 60 seconds old (five-second future tolerance).
- Fresh optional independently reported customer evidence, if present, must also
  be within 100 metres. Missing/stale evidence is recorded, not silently made a
  new mandatory customer interaction. Physical outside-handover acknowledgment
  is always required. Raw client GPS is not tamper-proof presence evidence.
- Vehicle requires the order's saved vehicle still belonging to the customer.
  Walk-in requires no vehicle. Both are outside-shop staff handover, not home
  delivery or inside-counter collection.
- Atomic shop/order/payment/location locks plus one-per-order audit prevent
  repeated/concurrent mutation. Completion does not deduct stock again.

Exact representative errors:

```json
{"customer_id":"Customer does not match this order."}
```

400 wrong customer; missing/false acknowledgment gives a corresponding field
array (different DRF validation layer). Unknown field 400:

```json
{"non_field_errors":["Unexpected request fields."]}
```

409:

```json
{"code":"order_not_ready","detail":"Order must be ready and awaiting handover."}
```

402:

```json
{"code":"payment_required","detail":"Verified captured payment is required."}
```

403:

```json
{"code":"approval_required","detail":"Recorded Super Admin approval is required."}
```

```json
{"code":"shop_restricted","detail":"Shop eligibility or existing-order review does not permit completion."}
```

400 location errors:

```json
{"code":"stale_staff_location","detail":"Obtain a fresh foreground staff location."}
```

```json
{"code":"outside_handover_radius","detail":"Staff must be within 100 metres of the shop."}
```

```json
{"code":"customer_outside_radius","detail":"Fresh customer location is outside 100 metres."}
```

Invalid mode/vehicle/shop coordinates have 409 code/detail; malformed/finite-range
coordinate input has 400 field errors. Throttle is 30/minute with 429 detail.
Do not string-match 404 wording; handle its status and safe detail.

Audit: StaffHandoverConfirmation records order, actor, server confirmation time,
pickup mode, checked customer ID, four acknowledgments, staff distance/capture
time, optional customer distance and evidence status. Application model/queryset
guards prohibit updates/deletes and the order/actor are protected foreign keys.
Order pickup_verified/at/by are persisted; one notification is created. Terminal
orders clear optional exact coordinates/live sharing. This audit is not proof
against privileged DB tampering or dishonest staff.

Existing optional Customer evidence API (not a completion API):

POST /api/orders/{id}/handover-location/ with owning Customer JWT and:

```json
{"latitude":"18.520430","longitude":"73.856744","location_captured_at":"2026-11-17T09:00:00Z"}
```

HTTP 200:

```json
{"order_id":456,"within_radius":true,"distance_to_shop_m":0.0,"pickup_radius_m":100}
```

It checks ready/paid/approval/shop eligibility and freshness. An outside fresh fix
returns within_radius=false and blocks completion while fresh. Staff cannot post
it under the customer's identity. No continuous/background GPS requirement.

## 7. Customer Flutter implementation order

1. Preserve API-origin configuration, existing JWT transport/secure storage,
   refresh/session-revision checks, public reset routes and account-expiry handling.
2. Remove phone/SMS login and Firebase phone exchange. Keep contact phone fields.
   Treat 410 as a retired feature/update instruction, never retry as login success.
3. Implement/retain email/password and email OTP exactly as above. Registration,
   send-OTP and forgot-password success do not contain login tokens.
4. Integrate Google/Apple ID-token exchanges. Parse nested user only on /api/auth/
   login success; parse password-login tokens and /me separately. Enforce customer
   role on login and restore; never derive role from provider/local account labels.
5. Keep full Customer profile, DOB/21+ declaration, address readiness and checkout
   gates. /me has date_of_birth/phone, while the new login DTO uses
   birth_date/mobile_number. Do not conflate either with documentary age proof.
6. Preserve cart refresh, shop consistency, server prices and explicit Walk-in /
   Vehicle selection. Vehicle needs a saved owned ID; Walk-in omits it. Preserve
   fresh preview/create GPS, 100m, minimum subtotal 500, fee 50 once and total-based
   15000 approval boundary. Keep paise precision and uncertain-create recovery.
7. Remove handover OTP display/read/issue/reissue/copy/cooldown and related UI.
   Ready screen says wait outside on foot/at the selected vehicle for staff.
8. Display only backend-authoritative order state. Customer has no completion
   operation; refresh history/detail/tracking when staff completes. Preserve mode,
   vehicle, payment/reconciliation, cancellation and existing foreground tracking.
9. If existing foreground GPS is available on a ready order, optionally post the
   existing handover-location evidence. Do not add blocking background tracking
   or a new mandatory acknowledgment step.
10. Keep inbox/preferences/support/profile/cart/vehicle functionality and clear
    account-bound state on logout/account switch. Do not enable live payments.

## 8. Shop Admin Flutter implementation order

1. Preserve role gate, existing JWT session helpers and shop/license restrictions.
2. Remove customer handover-code input/verification UI and calls to legacy OTP,
   verify-pickup or complete endpoints. Their 410 response cannot mean completed.
3. For both modes, show confirmation only on backend-ready, paid/captured,
   approved and eligible owning-shop orders. Use total including fee and recorded
   approval; subtotal-only UI guards are incorrect. Backend remains final judge.
4. Show backend customer_id/name, order ID/number, mode and selected vehicle.
   Require four explicit acknowledgments and a fresh foreground staff fix at the
   actual outside-shop handover. Do not reuse shop-published or stale coordinates.
5. POST the exact staff payload once per explicit action; disable duplicate taps.
   Never send status="completed" or update local status optimistically.
6. On HTTP 200 verify matching order ID/status and refresh Django order detail/list.
   On timeout inspect order state; safe replay of the same genuine confirmation
   yields the recorded result. Do not fabricate completion for ambiguous failures.
7. Handle 400 GPS/acknowledgment errors, 402 payment, 403 approval/shop, 404 scope,
   409 state and 429 throttling. Refresh status on state/eligibility changes.
8. Preserve accept/reject, preparing/ready, inventory, products, availability,
   notifications, location management, reports and Test Mode payment gates.

No Customer or Shop Admin Flutter source was edited from this workspace.

## 9. Configuration readiness and variable names

Only variable names are listed here; configure values through the secret/provider
configuration process, never from this report or a Flutter bundle.

| Area | Current readiness | Environment variable names |
|---|---|---|
| Django/JWT | Explicit environment/debug and strong dedicated signing key absent; development fallback warning remains | DJANGO_ENVIRONMENT, DJANGO_DEBUG, DJANGO_SECRET_KEY, SIMPLE_JWT_SIGNING_KEY |
| Email OTP digest | Dedicated key absent locally; development uses existing secret fallback | EMAIL_OTP_HMAC_KEY |
| Database | Connection works; schema fully migrated; exclusive/non-shared ownership not attested | DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT |
| Google | Web/Android audiences present and syntax-valid; platform/live matching unverified | GOOGLE_OAUTH_CLIENT_IDS, GOOGLE_OAUTH_ANDROID_CLIENT_IDS |
| Apple | Unconfigured; missing audience list causes safe 503 | APPLE_CLIENT_IDS |
| Mail | SMTP credentials/host/sender absent; local console fallback only | EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, EMAIL_USE_SSL, DEFAULT_FROM_EMAIL |
| Password reset | Reset URL absent; generic request 200 currently sends no reset email | PASSWORD_RESET_URL, PASSWORD_RESET_TIMEOUT |
| Distributed throttling | Defaults to process-local memory; production shared cache not configured | DJANGO_CACHE_BACKEND, DJANGO_CACHE_LOCATION |
| Retained old phone proof | Preserve the existing HMAC key value for accurate proof metadata; it grants no authentication capability | `PHONE_PROOF_HMAC_KEY` |

Production requires `DJANGO_ALLOWED_HOSTS` (comma-separated hostnames, no schemes).
Set `DJANGO_CORS_ALLOWED_ORIGINS` and `DJANGO_CSRF_TRUSTED_ORIGINS` to the
specific HTTPS browser origins that need them. HTTPS redirects and secure cookies
are enabled in production. Set `DJANGO_TRUST_HTTPS_PROXY=1` only behind a proxy
that strips and sets `X-Forwarded-Proto` and disallows direct access. Configure
`DJANGO_MEDIA_ROOT` on persistent private storage; never serve the whole upload
tree through a static URL. `collectstatic` targets `staticfiles/`. Package only
runtime app source, migrations, templates, manage.py and runtime dependencies;
exclude workspace credentials, dumps, uploads, venvs and retained test clusters.
Validate the artifact manifest before upload. Google
requires actual Flutter platform/server client matching. Apple requires real
developer capability/audiences and domain/return-URL configuration as applicable.
Email requires verified sender/domain and a real controlled inbox smoke test.
Production console mail is explicitly rejected. Existing payment configuration
was not changed; no live payment credential was supplied or enabled.

## 10. Tests and reproducible evidence

The original selection was re-run, rather than trusting the earlier report:

| Run | Passed | Failed assertions | Errors | Skipped |
|---|---:|---:|---:|---:|
| Original post-implementation selection | 118 | 0 | 0 | 0 |
| Added exact-contract/error checks, before fixes | 5 | 0 | 2 | 0 |
| Combined selection after malformed-input fixes | 125 | 0 | 0 | 0 |
| Final selection including routed Shop DTO checks | 128 | 0 | 0 | 0 |

The two errors above were the logout array and invalid decoded reset UID;
neither was skipped, suppressed or changed into an expected failure. The new
tests also assert exact Google/Apple response keys, registration/password/me/
refresh/logout shapes, new-auth errors, and that generic forgot-password 200
does not prove email delivery when its URL is absent.

Original selection fresh-run evidence:
`tools/test_runs/auth_handover_jds1xf1m/manifest.json`.
Initial contract-failure evidence:
`tools/test_runs/auth_handover_syp7e338/manifest.json`.
Final 128-test verification evidence:
`tools/test_runs/auth_handover_qinob_4w/manifest.json`.
Each database/data directory is retained, with only that run's server stopped.

The final combined command uses the existing isolated runner with these labels:

```powershell
.\venv\Scripts\python.exe -u -B tools/run_auth_handover_tests.py accounts.test_mobile_auth accounts.test_final_auth accounts.test_flutter_contracts orders.test_staff_handover orders.test_flutter_handover_contracts accounts.tests.GoogleIdentityVerificationTests accounts.tests.GoogleAuthenticationTests accounts.tests.JWTAuthenticationTests accounts.tests.LogoutTests accounts.tests.PasswordRecoveryTests accounts.test_api_integration accounts.test_age_self_declaration orders.test_razorpay_gateway
```

The active configured-settings system check and migration-drift check were rerun
after the fixes: both passed, retaining the development signing-key warning.
The four verification-pass Python files parsed successfully. No new migration
was generated by the validation guards or additive read-only serializer field.

Coverage includes email OTP expiry/replay/throttling/concurrent single-use,
Google/Apple mocked verification and real synthetic RSA checks, wrong role/blocked
accounts, password/JWT/reset, phone retirement/account retention, Walk-in/Vehicle
completion, wrong shop/non-ready/unpaid/unapproved/restricted orders, fresh/stale/
outside location, explicit acknowledgments, immutable audit, concurrent completion,
checkout minimum/fee/geofence/high-value boundary and stock preservation.

This is a selected regression suite, not a claim that every historical test in
the repository is green. Legacy tests that expect successful retired SMS or
handover-OTP flows target the old contract. No live Google/Apple/SMTP or real
Flutter/device/payment flow was exercised in this verification.

## 11. Remaining blockers and release/integration order

1. Database schema is already aligned on the inspected target. Approve/check any
   other deployment separately; do not rerun migration data work blindly.
2. Configure strong secrets, explicit environment, shared cache, HTTPS/hosts/CORS
   and log/APM exclusions. Review the current development JWT warning.
3. Configure SMTP/sender and PASSWORD_RESET_URL, then test real email OTP and reset
   delivery in a controlled inbox. A console message/HTTP 200 is insufficient.
4. Configure Apple and validate Google client IDs against actual Flutter builds;
   test first/repeat/provider-collision/blocked cases on real devices.
5. Integrate Customer authentication/restore/profile and remove retired phone UI;
   coordinate signed-in legacy phone-account recovery without merging accounts.
6. Integrate Shop confirmation for both modes and remove both apps' handover OTP UI.
7. Exercise cart -> preview -> create -> approval where needed -> accept -> verified
   Test Mode capture -> preparing -> ready -> staff handover -> Customer tracking,
   plus rejection/cancellation/refund/reconciliation and network uncertainty.
8. Review GPS/staff-fraud limitations, optional-location retention/purge policy,
   privileged audit access and inactive old-client rollout before release.

Client GPS and staff acknowledgments can be falsified; application audit guards
do not prevent privileged database tampering. Missing/stale optional Customer GPS
is permitted and recorded, so this is not cryptographic proof of customer presence
inside 100m. A dishonest/mistaken authorized owner remains an operational risk.
Retained exact coordinates on abandoned nonterminal orders need an approved
retention policy. No background tracking or mandatory new Customer step was added.

Implementation and selected tests are verified separately from provider setup and
live workflows. Production readiness is not claimed.
