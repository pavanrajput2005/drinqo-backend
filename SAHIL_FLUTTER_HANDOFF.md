# DRINQO Flutter API Handoff (source-verified)

Prepared from the Django routes, views, serializers, settings, and tests in
this workspace. Examples use placeholders; do not treat them as real
credentials or account data.

Payment source update: [Razorpay payments](./docs/razorpay-payments.md) is the
current payment integration contract. Its backend is disabled by default and
Test Mode only; migration `0010_payment_ledger.py` is unapplied and tests for
this payment change have not run. Earlier verification counts below do not
validate this new payment/OTP integration. Live merchant/Route activation is
blocked pending the provider's eligibility decision and account onboarding.
Display one ₹50 **Platform fee**, using `platform_fee` with legacy
`application_fee` as fallback; both identify the same charge. There is no added
delivery charge. Required flow: order placed → Shop confirmed → customer
 payment verified → preparing → ready → paid-order staff-confirmed handover.
The former OTP source contract is historical only; use
`docs/final-auth-handover.md`. No Flutter files were changed in this task.

## Status and blockers

- Flutter project directories and Android Gradle files for Customer, Shop
  Admin, and Super Admin are not present in this workspace.
- The backend OAuth allowlist settings are syntactically valid in the current
  environment. Their client-ID values are intentionally not included here.
- This backend workspace cannot verify the Google Cloud project, app package
  registrations, signing fingerprints, or whether the Flutter `serverClientId`
  matches the configured audience. These are required before real-device
  Google login can be considered verified.
- The current source implements shop renewal upload and Super Admin review.
  **Renewal review caveat:** verifying a pending renewal for an `expired` shop
  changes that shop's `license_status` from `expired` to `verified` (and may
  update `license_number`). It does not set `is_active` or clear suspension.
  Rejection changes only the document verification status. Initial pending
  applications use the separate approve/reject endpoints.
- No real Google token or live mobile request was tested during this
  contract audit.
- Django commands emitted a warning that the current environment's development
  JWT signing key is shorter than 32 bytes. `manage.py check` passes, but the
  deployment must supply and verify an appropriately strong
  `SIMPLE_JWT_SIGNING_KEY` before production use; the key value is not included
  here.

## Shared conventions

- API prefix is wired in `config/urls.py`.
- Protected APIs use `Authorization: Bearer <Django access JWT>`, not a Google or Firebase ID token.
- Supported `user_type` values are `customer`, `shop_admin`, `super_admin`.
  The backend is authoritative; never grant a role based on Flutter state or
  request data.
- Missing/invalid JWTs normally return HTTP `401`; an authenticated account
  without the required role receives HTTP `403`.
- The custom user model has `email` as `USERNAME_FIELD`.

## 1. Account and Admin `/me/`

### `GET /api/accounts/me/`

Requires a valid Django access JWT. `MeView` returns exactly:

```json
{
  "id": "<django-user-id>",
  "firebase_uid": "<stored Firebase UID or null>",
  "email": "<Django account email>",
  "phone": "<Django account phone>",
  "user_type": "shop_admin",
  "is_verified": false,
  "age_verified": false,
  "date_of_birth": null,
  "age_verified_at": null
}
```

This response does **not** contain `is_active`, a shop ID, or shop ownership
fields. Do not infer activity from this response.

`age_verified` is true only when an approved `age_proof` session has been
reviewed by a Super Admin and the confirmed DOB establishes age 21 or older.
The `POST /api/accounts/age-verification/` DOB remains self-reported and does
not grant eligibility. Submit proof through
`POST /api/customer/verification/sessions/` with multipart
`document_type=age_proof` and `document`; checkout remains blocked until
approval. The profile's DOB field is read-only.

### Shop Admin identity: `GET /api/shop-admin/me/`

This is the separate endpoint for the authenticated Shop Admin's shop. It
returns:

```json
{
  "user_id": "<django-user-id>",
  "email": "<Django account email>",
  "user_type": "shop_admin",
  "shop": {
    "id": "<shop-id>",
    "shop_name": "<shop-name>",
    "phone": "<shop-phone>",
    "email": "<shop-email>",
    "address_line1": "<address>",
    "address_line2": "",
    "city": "<city>",
    "state": "<state>",
    "postal_code": "<postal-code>",
    "license_status": "verified",
    "is_active": true,
    "is_open": true,
    "opening_hours": {},
    "latitude": "<decimal-or-null>",
    "longitude": "<decimal-or-null>"
  }
}
```

The view resolves the shop using `owner=request.user`; it does not return an
`owner_id`. The endpoint requires JWT authentication and
`IsApprovedActiveShopAdmin`: Shop Admin role, owned shop, verified/current
license, active and not suspended. A shop with no current verified license
cannot use this operational endpoint.

### Super Admin identity: `GET /api/super-admin/me/`

Requires a Django JWT and the Super Admin role. Response:

```json
{
  "id": "<django-user-id>",
  "email": "<Django account email>",
  "user_type": "super_admin",
  "is_active": true
}
```

`IsSuperAdmin` checks `user_type`; JWT authentication rejects inactive users.
All protected Super Admin views apply this role permission.

## 2. Authentication contracts

### Firebase authentication removal

The Firebase ID-token-to-Django-JWT exchange and Firebase-token password setup endpoints are no longer available. Existing `firebase_uid` values and user records are retained for data compatibility, but are not accepted for authentication. Email/phone matches do not automatically link accounts. An existing user must sign in using an available Django method and then link Google explicitly before Google login can be used.

### Google Login

`POST /api/accounts/google-login/`, no JWT required:

```http
Content-Type: application/json
```

Request:

```json
{"id_token":"<Google ID token>"}
```

Success is HTTP `200` and returns:

```json
{
  "id": "<django-user-id>",
  "email": "<stored Django email>",
  "phone": "<stored Django phone>",
  "user_type": "customer",
  "is_verified": false,
  "age_verified": false,
  "refresh": "<Django refresh JWT>",
  "access": "<Django access JWT>"
}
```

An already-linked Google subject retrieves the existing Django account and
preserves its role. A new Google registration is always `customer`. An
unlinked account with a matching email is rejected; it is not automatically
linked. Client-provided role fields do not grant roles.

Exact failure behavior:

| HTTP | JSON / condition |
|---|---|
| 400 | Missing, malformed, or too-long `id_token`: DRF field validation error, e.g. `{"id_token":["This field is required."]}` |
| 401 | Invalid/expired/untrusted ID token, unlinked email collision, inactive/blocked linked account, or unsupported linked role: `{"detail":"Unable to authenticate with Google."}` |
| 503 | Missing/invalid OAuth allowlist or unavailable verification infrastructure: `{"detail":"Unable to authenticate with Google."}` |

### Wrong role and inactive account

Google Login does not accept a requested role. The authenticated account's
persisted role is returned. A valid linked admin can use Google Login; a new
Google user cannot become an admin. Accessing a role-protected endpoint with a
valid token for the wrong role returns HTTP `403` with the endpoint's permission
message (for example `{"detail":"Super Admin access required."}`). Inactive
accounts are rejected by Google Login with the generic `401` above and by JWT authentication when accessing protected APIs with `401`.

## 3. Explicit Google account linking

`POST /api/accounts/google-link/` requires an authenticated Django access JWT
and a fresh Google ID token (issued within five minutes):

```http
Authorization: Bearer <Django access JWT>
Content-Type: application/json
```

```json
{"id_token":"<fresh Google ID token>"}
```

Success is HTTP `200`:

```json
{"detail":"Google account linked successfully."}
```

If the same identity is already linked to this user, HTTP `200`:

```json
{"detail":"Google account is already linked."}
```

Errors:

| HTTP | JSON / condition |
|---|---|
| 400 | Missing/malformed `id_token`: DRF field error |
| 401 | Missing/invalid/expired JWT; or invalid Google identity: `{"detail":"Unable to link Google account."}` for the latter |
| 403 | Authenticated but ineligible account: `{"detail":"Unable to link Google account."}` |
| 409 | User has a different Google identity or this subject belongs to another account: `{"detail":"Unable to link Google account."}` |
| 503 | Google verification/database unavailable, with detail `Google verification is temporarily unavailable.` or `Google account linking is temporarily unavailable.` |

Matching email is not used to select the Django account. The authenticated
user is the account being linked. Linking updates only `google_sub`; it
preserves Django user ID, email, password, role, Firebase UID, and associated
Shop/Order relationships. A duplicate subject cannot be overwritten.

## 4. Google OAuth configuration

Verified backend policy:

- `GOOGLE_OAUTH_CLIENT_IDS` is a comma-separated allowlist of OAuth client IDs
  permitted as the Google ID-token `aud`. Entries are trimmed and syntax
  checked; the backend fails Google verification closed if the allowlist is
  missing/invalid.
- For Flutter Google Sign-In, configure `serverClientId` to the **Web
  application OAuth client ID** used to request the ID token. That ID must be
  included in the backend `GOOGLE_OAUTH_CLIENT_IDS` allowlist. Do not put a
  Google Client Secret in Flutter.
- If an Android ID token has that Web client as `aud` and an Android OAuth
  client as `azp`, the Android client must be in
  `GOOGLE_OAUTH_ANDROID_CLIENT_IDS`. The backend still validates `aud` against
  the Web/audience allowlist and rejects an untrusted present `azp`.
- The backend verifies signature, issuer, expiration, audience, authorized
  party when present, subject, email syntax, and `email_verified=true`.
- Android OAuth registrations must be created for each exact Android
  application ID and signing certificate. Debug and release signing
  certificates have separate SHA-1/SHA-256 fingerprints and should be
  registered as needed for the corresponding build variants.

Current process configuration check: Web allowlist valid with one entry;
Android authorized-party setting valid with one entry. Client IDs are not
printed. This does not prove that the configured IDs belong to the intended
Google Cloud project or match the Flutter builds.

Not verifiable in this backend workspace:

- Customer, Shop Admin, and Super Admin Android application IDs and Gradle
  signing configurations (Flutter paths are absent).
- Debug/release SHA-1 and SHA-256 fingerprints.
- Correct Google Cloud project, credential ownership, OAuth consent/testing
  configuration, and the actual client ID configured as Flutter
  `serverClientId`.
- The pasted task mentions `com.drinqo.shopadmin` and
  `com.drinqo.superadmin`; these identifiers were not verifiable from local
  Android project files. Customer package ID was not provided in backend
  source.

Do not treat OAuth registration, Android `azp` trust, or real Google login as
production-verified until Sahil supplies the app/build fingerprints and a
real-device end-to-end test passes.

## 5. JWT refresh and logout

### Refresh

`POST /api/accounts/token/refresh/`:

```http
Content-Type: application/json
```

Request:

```json
{"refresh":"<Django refresh JWT>"}
```

Success is HTTP `200`:

```json
{"access":"<new Django access JWT>"}
```

Refresh tokens are **not rotated**. Access lifetime is five minutes; refresh
lifetime is one day. Invalid, expired, and blacklisted refresh tokens return
HTTP `401` in SimpleJWT's token-not-valid error shape. The `detail` reflects
the token failure:

```json
{"detail":"Token is invalid","code":"token_not_valid"}
```

For an expired token, `detail` is `"Token is expired"`; for a blacklisted token,
it is `"Token is blacklisted"`.

Inactive account refresh is rejected by SimpleJWT's authentication rule with
HTTP `401`:

```json
{"detail":"No active account found with the given credentials","code":"no_active_account"}
```

A blocked customer is rejected by the custom refresh serializer with HTTP
`403`:

```json
{"detail":"This customer account is blocked."}
```

### Logout

`POST /api/accounts/logout/` requires both a valid access JWT and the matching
refresh token:

```http
Authorization: Bearer <Django access JWT>
Content-Type: application/json
```

```json
{"refresh":"<Django refresh JWT>"}
```

Success: HTTP `205 Reset Content`, empty body. The refresh token is
blacklisted. Errors:

| HTTP | JSON |
|---|---|
| 401 | Standard JWT authentication error for missing/invalid/expired access JWT |
| 400 | `{"refresh":"This field is required."}` |
| 400 | `{"refresh":"Invalid or expired refresh token."}` |
| 403 | `{"detail":"Refresh token does not belong to this user."}` |

The refresh-token blacklist app is installed. Refresh rotation and
blacklisting-after-rotation are both disabled; explicit logout blacklists the
submitted refresh token.

## 6. Shop location and license APIs

### Update Shop Admin shop

`PATCH /api/shop-admin/shop/` requires a Django access JWT and an
approved/active Shop Admin whose owned shop satisfies current license and
suspension checks. It is scoped to the authenticated owner.

Accepted fields are optional: `phone`, `email`, `address_line1`,
`address_line2`, `city`, `state`, `postal_code`, `opening_hours`, `is_open`,
`latitude`, `longitude`. Coordinates use decimal values, latitude `-90..90`,
longitude `-180..180`.

Example request:

```json
{"latitude":"<latitude>","longitude":"<longitude>"}
```

Success `200`:

```json
{
  "detail":"Shop information updated.",
  "shop_id":"<shop-id>",
  "updated_fields":["latitude","longitude"],
  "shop":{"latitude":"<decimal-or-null>","longitude":"<decimal-or-null>"}
}
```

Errors:

- `400 {"detail":"A JSON object is required."}` for a non-object request.
- `400 {"detail":"No editable fields supplied.","editable_fields":[...]}`
  for an empty object. The list is the accepted field set above.
- `400 {"detail":"Unknown fields.","fields":["<field>"]}` for unsupported keys.
- Invalid coordinate values return DRF field errors, e.g.
  `{"latitude":["Ensure this value is less than or equal to 90."]}` or
  `{"longitude":["A valid number is required."]}`.
- `401` invalid/missing access JWT; `403` wrong role or shop not eligible;
  `404 {"detail":"No shop is assigned to this Shop Admin."}` when no shop is
  assigned.

`GET /api/shop-admin/me/` returns the saved coordinates from the same
`shops.Shop` record. Customer list/nearby APIs serialize that record's current
coordinates only when the shop is eligible.

### Renewal document upload

`POST /api/shop-admin/application/documents/` requires a Django access JWT and
Shop Admin role. Use `multipart/form-data` fields:

- `license_number`
- `expiry_date` (`YYYY-MM-DD`, must be in the future)
- `license_document` (PDF/JPG/JPEG/PNG, at most 10 MB)

Only an existing Shop Admin-owned shop with `license_status` `verified` or
`expired` can submit a renewal. Success is HTTP `201`, with document ID and
pending status:

```json
{
  "id":"<document-id>",
  "license_number":"<license-number>",
  "expiry_date":"<YYYY-MM-DD>",
  "verification_status":"pending",
  "verified_at":null,
  "created_at":"<ISO-8601 timestamp>",
  "download_url":"<API-origin>/api/shop-admin/application/documents/<document-id>/download/"
}
```

Validation errors are HTTP `400` field errors; exact messages include
`"This license number is already in use."`,
`"License document must be 10 MB or smaller."`,
`"Use a PDF, JPG, or PNG document."`, and
`"License document must not be expired."`. Wrong role is `403
{"detail":"Shop Admin access required."}`; missing owned shop is `404`; an
unapproved application state is `400
{"detail":"License renewals are available only for approved shops."}`.

### Application document detail and download

- `GET /api/shop-admin/application/documents/{document_id}/` requires a
  Django JWT and Shop Admin role; it is scoped to the owning Shop Admin.
  Wrong role: `403 {"detail":"Shop Admin access required."}`. Missing or
  another owner's document: `404 {"detail":"Not found."}`.
- `GET /api/shop-admin/application/documents/{document_id}/download/`
  requires a Django JWT. It allows the owning Shop Admin or a Super Admin;
  other authenticated users receive `403
  {"detail":"You cannot access this license document."}`. Success is a file
  download, not JSON. Missing document is 404.

The separate legacy `/api/shop-admin/licenses/...` routes use the legacy
`shop_admin` models and are not the renewal upload model/path above.

### Verify/reject a renewal document

`POST /api/super-admin/shops/{shop_id}/license-documents/{document_id}/review/`
requires a Django access JWT and Super Admin role.

Verify:

```json
{"decision":"verify"}
```

Reject:

```json
{"decision":"reject","reason":"<reason>"}
```

Success `200`:

```json
{
  "shop_id":"<shop-id>",
  "document_id":"<document-id>",
  "verification_status":"verified",
  "license_status":"verified",
  "is_active":true
}
```

For rejection, `verification_status` is `"rejected"` and the other fields
reflect the shop's existing status. Errors:

- `401`: missing/invalid JWT.
- `403 {"detail":"Super Admin access required."}`: wrong role.
- `400 {"detail":"Choose verify or reject."}`: invalid decision.
- `400 {"reason":"A reason is required when rejecting."}`: reject without
  reason.
- `400 {"detail":"Initial applications must be reviewed through the shop application approval endpoint."}`: shop is not `verified` or `expired`.
- `400 {"document":"An expired license document cannot be verified."}`:
  expired renewal.
- `404 {"detail":"Not found."}`: shop/document missing, document belongs to a
  different shop, or document is not pending.
- Unknown request fields or wrong JSON shape return `400` with
  `{"detail":"Unknown fields.","fields":[...]}` or
  `{"detail":"A JSON object is required."}`.

This review endpoint is for renewals, not initial approval. On a
`verified` shop, verifying a renewal changes the pending document to verified
and may update the shop's license number; it does not change `is_active` or
suspension. On an `expired` shop, it additionally changes `license_status`
from `expired` to `verified`, but still does not activate the shop. Rejecting
changes only the document verification status. Initial pending application
approval is a separate `POST
/api/super-admin/shop-applications/{application_id}/approve/` action and is
the operation that changes the application/license status and shop activation.

## 7. Super Admin shop application detail

### `GET /api/super-admin/shop-applications/{application_id}/`

Requires a Django access JWT and `super_admin` role. Success `200` has this
exact top-level schema:

```json
{
  "id":"<shop-id>",
  "shop_name":"<shop-name>",
  "owner_name":"<owner-name>",
  "owner_email":"<owner-email>",
  "phone":"<shop-phone>",
  "city":"<city>",
  "state":"<state>",
  "license_number":"<license-number>",
  "license_status":"pending",
  "application_status":"pending",
  "submitted_at":"<ISO-8601 timestamp>",
  "documents":[
    {
      "id":"<document-id>",
      "license_number":"<document-license-number>",
      "expiry_date":"<YYYY-MM-DD>",
      "verification_status":"pending",
      "verified_at":null,
      "created_at":"<ISO-8601 timestamp>",
      "download_url":"<API-origin>/api/shop-admin/application/documents/<document-id>/download/"
    }
  ],
  "status_history":[
    {
      "id":"<audit-id>",
      "actor_id":"<django-user-id>",
      "actor_email":"<actor-email>",
      "action":"<action>",
      "summary":"<summary>",
      "created_at":"<ISO-8601 timestamp>"
    }
  ],
  "license_details":{
    "license_number":"<license-number>",
    "license_status":"pending"
  }
}
```

`status_history` and `documents` may be empty arrays. The detail endpoint
returns `404 {"detail":"Not found."}` for an unknown application. Missing or
invalid JWT returns `401`; an authenticated non-Super Admin returns `403
{"detail":"Super Admin access required."}`.

The renewal review endpoint cannot approve a pending application: it returns
the `400` state error above. Pending applications use the distinct approve,
reject, or request-changes endpoints. **Caveat:** renewal verification of an
expired shop does change the shared `license_status`/`application_status`
value to `verified`; therefore the literal claim that license verification
never changes the application-status field would be false for that expired
renewal case. It does not set the shop active. Treat this distinction
explicitly in the UI.

## Flutter implementation sequence

1. Configure each Google Android OAuth client using the exact package ID and
   debug/release signing fingerprints supplied by the app owner; use the
   appropriate Web client as `serverClientId`.
2. Obtain a Google ID token and call Google Login. For existing admin accounts
   without a Google link, authenticate through an already-supported method
   first, then call Google Link with the Django access JWT and a fresh Google
   ID token.
3. Existing accounts without a linked Google subject must first authenticate
   through an available Django login method and then use the authenticated
   Google Link endpoint. Do not automatically link by email or phone.
4. Persist Django access/refresh tokens using platform-secure storage. Call
   `/api/accounts/me/` to obtain role state, then route the UI; do not infer or
   request roles from the client.
5. Implement refresh and logout exactly as specified. On `401`, clear local
   session state and require reauthentication.
6. For Shop Admin, load `/api/shop-admin/me/`, update location through
   `/api/shop-admin/shop/`, submit renewal documents if applicable, and refresh
   shop state after changes.
7. For Super Admin, inspect application detail and document download before
   review. Surface verify/reject status and keep initial application approval
   separate from renewal review.
8. Test role denial, expired/inactive accounts, wrong app credentials,
   Android `azp`, network failures, and refresh/logout on real builds.

## Verification run for this handoff

- Focused Django Google/JWT, role, customer profile/vehicle, checkout,
  order, push-disabled, and Super Admin approval tests: **98 passed** using an
  in-memory SQLite test database.
- A broader 155-test SQLite run exposed two concurrency tests that returned
  `503` instead of their expected conflict responses under SQLite. The
  report-total assertion was made database-independent and passed in the
  focused rerun. The earlier PostgreSQL test process was interrupted before
  completion; current validation used SQLite only.
- `manage.py check`: passed with no issues.
- `manage.py makemigrations --check --dry-run`: no changes detected.
- No migration or existing order rows were changed. The high-value approval
  comparison now uses `total_amount`; the ₹1,000 product-subtotal minimum and
  separate ₹50 application fee are unchanged. FCM push delivery is disabled.

## Integration decision

**GO** to begin Google OAuth/JWT integration against these endpoint contracts.
Push delivery is unavailable, though in-app notifications and legacy token
records remain. Do not treat push-token registration as push delivery. Flutter
integration and real-device validation have not been performed.
