# Final authentication and outside-shop handover contract

## Implemented scope and deployment boundary

Phone/SMS login and customer/staff handover OTP are retired. Email OTP remains.
Google/Apple identity proofs are exchanged for Django JWTs; protected APIs never
accept a Google/Apple token in place of a Django access token. No live payment
mode, order amount, stock policy, checkout age/address policy, or shop-review
policy was enabled or weakened.

Contact-number edits remain supported after SMS retirement. A changed contact
number is not certified by an older phone proof; phone_verified is true only
when current contact data matches the retained historical identity hash.
verified_phone_last4 remains historical proof metadata, not proof of a new number.

The configured database was inspected using a read-only connection only. It has
the correct accounts_user columns (including user_type and all mobile auth
fields), accounts migrations through 0011_mobile_auth and orders through 0012.
The historical misplaced USERNAME_FIELD/user_type defect is absent: both are
inside User. Its earlier cause was a Python class-boundary placement error;
there is no reason to recreate User or rewrite the already-applied migration.
No existing users/roles/passwords/provider links/profiles/orders were changed.

## Exact files changed in this implementation

Created:

- accounts/retired.py
- accounts/contact_identity.py
- accounts/migrations/0012_secure_email_otp.py
- accounts/test_final_auth.py
- orders/staff_handover.py
- orders/staff_handover_views.py
- orders/migrations/0013_staff_confirmed_handover.py
- orders/test_staff_handover.py
- tools/run_auth_handover_tests.py
- docs/final-auth-handover.md

Modified:

- accounts/models.py
- accounts/mobile_views.py
- accounts/mobile_urls.py
- accounts/urls.py
- accounts/authentication.py
- accounts/serializers.py
- accounts/retired.py (compatibility responses; no SMS provider)
- accounts/test_mobile_auth.py
- accounts/test_age_self_declaration.py
- config/settings.py
- config/mobile_auth_test_settings.py
- customers/serializers.py
- orders/models.py
- orders/signals.py
- orders/serializers.py
- orders/urls.py
- orders/staff_handover.py
- shop_admin/urls.py
- shop_admin/api_serializers.py
- docs/mobile-auth.md

The Google/Apple verifier modules were reviewed and retained. The existing
requirements.txt already lists Django, DRF, SimpleJWT, google-auth, requests,
PyJWT and cryptography. No package versions were changed or installed here.
Generated test-cluster directories under tools/test_runs are retained evidence,
not application files. No .env, production database or existing migration changed.

## Forward migrations

1. accounts.0012_secure_email_otp expands otp_code to hold an HMAC-SHA256 digest,
   adds a purpose and optional requesting_user binding for email migration, and
   invalidates/clears existing transient plaintext email OTPs. Users must request
   a fresh email code after rollout. No phone identities or historical handover
   records are deleted.
2. orders.0013_staff_confirmed_handover creates optional customer foreground
   location evidence and a one-per-order StaffHandoverConfirmation audit model.
   Old OrderHandoverOTP and HandoverAuditEvent tables remain intact.

Apply during a coordinated backend/Flutter rollout, after backup/config review:

```powershell
python manage.py check
python manage.py migrate --plan
python manage.py migrate
python manage.py makemigrations --check --dry-run
```

These commands are deployment instructions, not a record of execution against
the shared database. The new migrations were applied only in fresh isolated
test databases. Deploy code and schema together before routing production traffic.
Rollback of the schema cannot restore invalidated old plaintext email codes.

## Authentication endpoints and exact JSON shapes

All requests use application/json. Values shown are examples, not credentials.

### Google

POST /api/auth/google/

```json
{"id_token":"<Google ID token>"}
```

HTTP 200 uses the common login response below, with auth_provider="google" for a
new account. Existing linked subjects preserve user ID and role. Email collisions
never silently link accounts. Legacy POST /api/accounts/google-login/ remains;
it uses the same verifier but retains its existing flat response contract.

### Apple

POST /api/auth/apple/

```json
{"identity_token":"<Apple ID token>","first_name":"Example","last_name":"Customer"}
```

Name fields are optional. HTTP 200 uses the common login response, with
auth_provider="apple" for a new account. A new subject needs a verified email;
an already linked Apple subject may sign in without a repeated email claim.
Private-relay email is supported. Name input is display metadata, not identity
proof. No Apple-link endpoint is implied: an existing unrelated email collision
must use the existing sign-in method/support, not an automatic merge.

### Send email OTP

POST /api/auth/send-otp/

```json
{"email":"customer@example.com"}
```

HTTP 200:

```json
{"message":"OTP sent successfully"}
```

No JWT is returned before ownership verification. This intentionally preserves
the existing send-OTP contract rather than interpreting "all auth endpoints
return JWT" as authorization to issue a token before authentication.

### Verify email OTP

POST /api/auth/verify-otp/

```json
{"email":"customer@example.com","otp":"012345"}
```

HTTP 200 uses the common login response. New accounts are customers with
auth_provider="email" and an unusable password. An existing eligible customer
keeps their identity and original signup provider. Existing Shop/Super Admin
accounts cannot use this customer email-OTP flow.

An unverified existing account with a password or a linked provider returns
409 after mailbox proof; the challenge is consumed and no JWT is issued.
Use the existing sign-in method or password recovery. OTP does not adopt
previously enrolled credentials or merge accounts.

Six ASCII digits are required as a string. Codes expire at five minutes, are
single-use and generated with secrets.randbelow. The database stores only a
keyed digest bound to normalized email, purpose and requesting user. The raw code
exists only in memory/email delivery. A locked durable state row limits resends
to 60 seconds, five sends/hour and ten verifications/hour per address, independently
of the IP throttle; consuming/resending a code does not reset that budget.

### Common new-login success response

```json
{
  "access":"<django_access_jwt>",
  "refresh":"<django_refresh_jwt>",
  "is_profile_complete":false,
  "user":{
    "id":123,
    "email":"customer@example.com",
    "first_name":"",
    "last_name":"",
    "birth_date":null,
    "mobile_number":null,
    "auth_provider":"google",
    "is_profile_complete":false,
    "user_type":"customer"
  }
}
```

Values are dynamic; auth_provider records the original signup provider. Dates
are YYYY-MM-DD or null. Profile completion here means DOB/mobile completion,
not eligibility to bypass DRINQO address, age declaration or checkout checks.
PATCH /api/user/profile/ keeps its existing request and user-object response:

```json
{"birth_date":"1990-01-01","mobile_number":"9876543210"}
```

### Password/session/recovery endpoints retained

| Method/path | Request | Success |
|---|---|---|
| POST /api/accounts/register/ | username,email,phone,password | 201; username,email,phone; no JWT |
| POST /api/accounts/login/ | `{"email":"customer@example.com","password":"<password>"}` | 200; `{"refresh":"<JWT>","access":"<JWT>"}` |
| POST /api/accounts/token/refresh/ | `{"refresh":"<JWT>"}` | 200; `{"access":"<new JWT>"}` |
| POST /api/accounts/logout/ | `{"refresh":"<JWT>"}`, authenticated owner | 205, no body; refresh is blacklisted |
| GET /api/accounts/me/ | Bearer access token | 200; existing flat user fields |
| POST /api/accounts/password-reset/ | `{"email":"customer@example.com"}` | 200; generic eligibility message |
| POST /api/accounts/password-reset/confirm/ | uid,token,password,confirm_password | 200; `{"detail":"Password reset successfully."}` |
| POST /api/accounts/google-link/ | id_token; Bearer access | 200; existing link message |

Registration input example:

```json
{"username":"customer123","email":"customer@example.com","phone":"9876543210","password":"<strong password>"}
```

Registration success is exactly:

```json
{"username":"customer123","email":"customer@example.com","phone":"9876543210"}
```

It does not include JWTs. Existing Google-login success is flat:

```json
{"id":123,"email":"customer@example.com","phone":"9876543210","user_type":"customer","is_verified":false,"age_verified":false,"refresh":"<JWT>","access":"<JWT>"}
```

Password-reset request success:

```json
{"detail":"If an account with that email is eligible, password reset instructions will be sent."}
```

GET /api/accounts/me/ HTTP 200 retains this structure (dynamic values):

```json
{"id":123,"firebase_uid":null,"email":"customer@example.com","phone":"9876543210","user_type":"customer","is_verified":false,"age_verified":false,"age_self_declared":false,"age_self_declared_at":null,"date_of_birth":null,"age_verified_at":null}
```

Reset confirmation request:

```json
{"uid":"<encoded user ID>","token":"<reset token>","password":"<new password>","confirm_password":"<same password>"}
```

POST /api/accounts/set-password/ is now available to an authenticated account
with a verified non-placeholder email. Request:

```json
{"password":"<new strong password>","confirm_password":"<same password>"}
```

If the account already has a usable password, current_password is required too.
HTTP 200:

```json
{"detail":"Password set successfully. Sign in again."}
```

Setting/resetting password blacklists outstanding refresh tokens. Existing
access JWTs remain valid until their five-minute expiry; logout likewise revokes
the refresh token, not every already-issued access token. Refresh lifetime remains
one day and rotation behavior is unchanged. Blocked accounts of every role are
now rejected by password login, refresh and JWT authentication.

## Phone retirement and legacy-account migration

These POST routes return 410 Gone, make no provider call and issue no JWT:

- /api/accounts/otp/send/
- /api/accounts/otp/verify/
- /api/accounts/phone-link/request/
- /api/accounts/phone-link/verify/
- /api/accounts/firebase-login/ (explicit retirement tombstone)

Body:

```json
{"code":"phone_otp_retired","error":"Phone/SMS OTP is retired. Use email/password, email OTP, Google or Apple. Existing signed-in users can link a verified email; otherwise contact support for account recovery."}
```

The retired SMS provider has no active configuration or provider call.
Historical code/phone identity tables are retained for review. No active
Firebase phone-to-JWT bridge exists; Firebase UIDs remain stored as history.

A still-authenticated phone-only customer can preserve the same user ID:

1. POST /api/auth/link-email/send-otp/ with `{"email":"real@example.com"}`.
   HTTP 200 uses the email-send success message; no JWT before proof verification.
2. POST /api/auth/link-email/verify-otp/ with
   `{"email":"real@example.com","otp":"012345"}`.
   HTTP 200 uses the common login response, preserving user ID, role, phone,
   provider links, customer profile and orders. Only the placeholder email changes.
3. Optionally set a password or explicitly link Google using the existing route.

Link-email requires Bearer JWT, a retained verified phone identity and an
unverified @otp.drinqo.invalid placeholder email. Proof is bound to the requesting
user and purpose; it cannot be used as a public-login OTP. Existing email owners
are never merged. A user without a valid session, password or already-linked
provider needs reviewed manual support recovery. Do not recreate/merge their
account or accept a caller-supplied matching phone as ownership proof. That
manual support review is an operational requirement, not a fabricated automated
recovery API. Coordinate migration notices before disabling old Flutter clients.

## Staff-confirmed handover contract

POST /api/shop-admin/orders/{id}/confirm-handover/

Requires Django Bearer JWT of the active, unblocked owning Shop Admin. There is
no separate delegated-staff role/table in the current application; no such access
was invented. Shop order responses now include customer_id for explicit matching.

Exact request:

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

Coordinates/time above are illustrative. Runtime must send a fresh foreground
staff fix; capture time may be at most 60 seconds old or five seconds ahead.
All acknowledgments must be actual JSON true, not strings/1. customer_id must
match the order. Unknown fields (including status="completed" or otp) are rejected.

Server checks occur under atomic shop -> order -> payment/location row locks:

- Owning shop and authenticated actor role; the order is ready and not completed.
- Walk-in or Vehicle mode; Vehicle still references this customer's saved vehicle.
- Total >=15000 has a recorded approval timestamp and Super Admin actor.
- Existing shop licensing/suspension and order-specific continuation-review rules.
- Authoritative captured payment, matching amount split, no refunded amount;
  a manual paid flag alone is insufficient.
- Approved shop coordinates and fresh staff-reported location within 100 metres.
- If fresh independently submitted customer location exists, it must also be
  within 100 metres. Missing/stale customer evidence does not silently introduce
  a new mandatory customer step; the staff physical/outside acknowledgment remains
  required and the missing/stale evidence status is recorded.

HTTP 200:

```json
{
  "order_id":456,
  "status":"completed",
  "pickup_mode":"walk_in",
  "pickup_verified":true,
  "pickup_verified_at":"2026-11-17T09:00:01Z",
  "pickup_verified_by":42,
  "handover_method":"staff_confirmation",
  "confirmation_id":9,
  "already_completed":false
}
```

Vehicle uses pickup_mode="vehicle". An authorized replay of this completion
returns 200 with the original confirmation/actor/time and already_completed=true;
it creates no second audit/notification and changes no stock. Historic completed
orders without the new audit are not retroactively certified through replay.

Failures: 400 wrong/missing acknowledgment/customer/location; 401 missing/invalid
JWT; 403 wrong role/approval/shop restriction; 404 foreign-shop/missing order;
402 insufficient payment proof; 409 non-ready/terminal order; 429 rate limit.
Errors use existing DRF detail/code/field formats for order APIs, rather than
unnecessarily changing all existing order error contracts.

Optional customer foreground evidence (not completion):

POST /api/orders/{id}/handover-location/ with the owning customer's JWT:

```json
{"latitude":"18.520430","longitude":"73.856744","location_captured_at":"2026-11-17T09:00:00Z"}
```

HTTP 200:

```json
{"order_id":456,"within_radius":true,"distance_to_shop_m":0.0,"pickup_radius_m":100}
```

This is allowed only for eligible ready/paid orders. An outside fix is retained
with within_radius=false so fresh contrary evidence blocks staff completion. A
staff caller cannot submit this as customer-owned evidence. No background GPS is
required. Exact coordinates are cleared on completed/cancelled/rejected orders;
nonsecret distance/evidence status remains in the immutable application audit.

Old GET/POST handover-otp, POST handover-otp/reissue, POST verify-pickup and POST
shop-admin/orders/{id}/complete return 410 with:

```json
{"code":"handover_otp_retired","detail":"Handover OTP is retired. The owning Shop Admin must explicitly confirm outside-shop handover."}
```

Legacy challenge/verification services have been removed. Historical OTP
records/audit events remain; current OrderSerializer.handover_challenge_id is
always null as a compatibility tombstone. Do not call old OTP APIs on that basis.

## Flutter Customer handoff

- Remove phone/SMS login UI and Firebase phone exchange. Keep email/password,
  email OTP, Google and Apple. Never send provider tokens to protected order APIs.
- Use /api/auth/ success nested user contract; retain old flat parsing only for
  legacy accounts endpoints. Send-OTP has no JWT. Store Django tokens securely.
- Keep address/DOB/age gates and explicit Walk-in/Vehicle selection. Show both
  as outside-shop handover; Vehicle uses a saved owned vehicle, Walk-in no vehicle.
- Remove OTP issue/read/reissue/display/cooldown/copy screens and OTP secure-window
  assumptions. Ready status says wait outside for staff; completion comes only
  from backend tracking/history, never from a local button.
- If fresh foreground GPS is already available, optionally report it through
  handover-location; do not silently add a blocking customer step/background task.
- Preserve preview/create fresh-GPS checks, 100m, 500 minimum, 50 fee and uncertain
  create recovery. Refresh parent tracking after backend completion.
- Handle 410 with update/sign-in migration guidance, not retry or local success.

## Flutter Shop Admin handoff

- Both Walk-in and Vehicle get the same prepare -> ready -> confirm handover
  flow. Replace vehicle-only Verify OTP UI with an explicit confirmation dialog.
- Show backend order number, customer identity and selected vehicle/mode. Require
  staff to check the customer/order and acknowledge actual outside-shop handover.
- Acquire a fresh foreground staff location immediately for the POST; show
  permission/error recovery. Do not use shop-published live location or stale
  coordinates as staff GPS.
- Send exact booleans, customer_id and location payload above. Disable duplicate
  taps; after uncertain network results refresh order and safely replay only the
  same explicit completed handover action as needed. No auto-completion on GET.
- Trust only authoritative completion responses. High-value guards must use
  total including fee and recorded backend approval, not subtotal-only checks.
- Keep payment gates, stock controls and restricted-shop review states intact.

## Provider and production configuration still required

The inspected local configuration has syntactically valid Google Web/Android
audience IDs, but no Flutter OAuth/client-console comparison or live Google test
was performed. Match the actual Flutter server/Web client ID to the backend
audience allowlist and Android authorized-party IDs; configure platform signing
and consent/origins in the provider console.
[Google backend authentication guidance](https://developers.google.com/identity/sign-in/android/backend-auth).

APPLE_CLIENT_IDS is absent locally, so Apple fails closed with safe 503. Configure
the actual Sign in with Apple capability/App ID for native use; web/other-platform
flows require the appropriate Services ID and registered domain/return URLs. Add
only the resulting real bundle/Services audiences to APPLE_CLIENT_IDS. This backend
verifies identity tokens; authorization-code exchange/client-secret generation
is not claimed by these endpoints. No Apple credentials were invented.
[Apple configuration](https://developer.apple.com/help/account/capabilities/configure-sign-in-with-apple-for-the-web/),
[Apple token verification](https://developer.apple.com/documentation/signinwithapple/verifying-a-user).

SMTP is not configured locally. For real delivery set EMAIL_BACKEND to Django SMTP,
EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, DEFAULT_FROM_EMAIL,
EMAIL_USE_TLS/EMAIL_USE_SSL according to the provider; never both TLS and SSL.
Keep credentials in the environment/secret store. Verify the sender/domain and
Apple private-relay sender requirements if applicable. Console mail is allowed
only in local development, never as a failed-SMTP fallback in production.

Production also needs independent random DJANGO_SECRET_KEY, SIMPLE_JWT_SIGNING_KEY
and EMAIL_OTP_HMAC_KEY (at least 32 bytes), explicit production/debug settings,
HTTPS/hosts/CORS and a shared throttling cache. Rotating the OTP HMAC key invalidates
outstanding email codes, not existing customer accounts.
The actual local `manage.py check` passed, with a development warning that the
fallback JWT signing key is shorter than 32 bytes. An explicit strong production
signing key is still required; that warning was not suppressed or fixed with an
invented secret.

Real-delivery smoke test: use a controlled inbox, request a code, confirm it arrives
without appearing in HTTP/logs, verify once, reject replay, reject a code after
five minutes and confirm resend cooldown. Do not put the real code in reports.
Google/Apple need real-device tests with correct platform IDs, first and repeat
authorization, private relay, invalid tokens and account collisions. Payments remain
Test Mode only and need controlled capture/refund/inventory round trips separately.

## Security limits

Removing the customer OTP removes a customer-held secret from physical handover.
A dishonest/mistaken authorized owner can still attest falsely. Explicit
customer/order matching, foreground GPS, immutable application audit and customer
notification improve accountability; they are not proof the goods changed hands.
GPS timestamps/coordinates are client claims and can be spoofed. Fresh optional
customer evidence is attributed to their authenticated session but is not hardware
attestation. Customer acknowledgment may be considered later, not silently made
mandatory in this change.

Append-only model/queryset safeguards are application controls, not protection
from privileged database tampering. Restrict database/admin access and backups.
Optional customer coordinates on abandoned nonterminal orders need an operational
retention purge policy; stale evidence is never accepted for completion. Reverse
proxy/APM collectors must exclude auth/handover request bodies and tokens; Django
reporter suppression and sensitive-response hints do not configure external logs.

## Test evidence

Tests use newly initialized PostgreSQL 18 clusters in unique tools/test_runs paths,
loopback-only ports, fresh non-superuser roles/databases, synthetic mail/cache and
mocked/blocked external HTTP. The shared database is not used for tests. Every run
is retained; only its own new server is stopped. No PostgreSQL service or database
is dropped. The runner does not import base settings or load .env.

Run with the inspected virtual environment:

```powershell
.\venv\Scripts\python.exe -u -B tools/run_auth_handover_tests.py
```

The first sandbox startup was blocked by Windows restricted-token behavior. A
subsequent Windows subprocess-pipe hang was corrected to file-backed output and
only that new cluster was stopped. The first executed 90-test selection exposed
a synthetic ledger split-constraint fixture error, missing password validators in
synthetic settings, and an age empty-input error-key mismatch. These were corrected
without weakening payment/checkout checks. The next 92-test PostgreSQL run passed,
including simultaneous email verification and simultaneous staff completion.
The expanded checkout/gateway run passed 115 tests; registration/deleted-account
hardening brought that to 117. The hashing assertion was made non-probabilistic
and its focused PostgreSQL recheck passed. After the final contact-edit/retained
proof correction, the **final 118-test PostgreSQL selection passed with no skips**.
System checks reported no issues and migration drift reported "No changes detected".
Its retained test database is identified by
`tools/test_runs/auth_handover_0l_3i11_/manifest.json` (server stopped).
All 28 changed Python files passed final AST parsing.

Legacy test modules that assert successful phone/SMS OTP login or OTP-based
handover describe the retired contract. They are not production-readiness evidence
for the new contract; new retirement/hand-over suites explicitly assert 410 and
the new security gates. No old historical records were purged to satisfy tests.
