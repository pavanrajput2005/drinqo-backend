# Google Authentication and Flutter Integration

## Google OAuth configuration

Set `GOOGLE_OAUTH_CLIENT_IDS` in the backend environment to a comma-separated
allowlist of the OAuth client IDs that can be the audience of ID tokens
received by DRINQO. Use the actual IDs from the project's Google Cloud
configuration; do not invent or copy IDs from examples. Whitespace around
entries is trimmed, and each ID must use Google's
`.apps.googleusercontent.com` format.

For Android Google Sign-In, a valid ID token can have the configured Web
client ID as `aud` and the Android OAuth client ID as `azp`. Configure only
the verified Android OAuth client IDs in the separate comma-separated
`GOOGLE_OAUTH_ANDROID_CLIENT_IDS` backend environment variable. The backend
continues to require `aud` to match `GOOGLE_OAUTH_CLIENT_IDS`; a present `azp`
must match either an allowed audience client ID or an explicitly configured
Android client ID. An unconfigured or malformed Android list does not add any
trusted `azp` values. Obtain Android client IDs from the correct Google Cloud
project's Android OAuth clients, matched to each app's exact package name and
signing certificate; do not trust IDs merely because they appear in a token.

The Flutter Google Sign-In setup must request an ID token whose `aud` is one of
these configured IDs. Configure the appropriate OAuth clients for the Android,
iOS, and web/server-client flows used by the three apps, and include every
approved token audience in the backend allowlist. The backend validates the
token's signature, issuer, expiry, audience, subject, and verified email using
Google's signing certificates.

Google OAuth client IDs are public identifiers, not credentials. The backend
does not need a Google Client Secret to verify these ID tokens. Do not put
Google Client Secrets, Firebase service-account credentials, Django secrets,
or JWT signing keys in any Flutter app. If the allowlist is missing or
malformed, Google login and linking fail closed with HTTP `503`; other login
methods remain available.

## Authentication endpoints

All paths below are relative to the backend origin. Send JSON with
`Content-Type: application/json`.

### Google login

`POST /api/accounts/google-login/` is unauthenticated and accepts:

```json
{"id_token":"<Google ID token>"}
```

The ID token is sent in the request body, never as the API bearer token. A
successful response is HTTP `200`:

```json
{
  "id": 123,
  "email": "customer@example.com",
  "phone": "",
  "user_type": "customer",
  "is_verified": false,
  "age_verified": false,
  "refresh": "<Django refresh JWT>",
  "access": "<Django access JWT>"
}
```

Values are generated from the authenticated Django account; token strings,
user ID, and profile values vary. A new Google account is always created as a
customer with an unusable Django password. Client-supplied role or profile
fields are ignored. An existing account is selected only by its already-linked
Google subject. An email match alone never links or authenticates an existing
account.

Invalid tokens, email collisions, inactive/blocked accounts, and unlinked
existing accounts return HTTP `401`:

```json
{"detail":"Unable to authenticate with Google."}
```

Missing/malformed `id_token` input returns HTTP `400` with a field validation
error. Missing or invalid OAuth allowlist configuration, or unavailable
verification infrastructure, returns HTTP `503`:

```json
{"detail":"Unable to authenticate with Google."}
```

### Explicit Google account linking

An existing account must first authenticate using its existing method. Then
call `POST /api/accounts/google-link/` with the current Django access JWT and a
fresh Google ID token (issued within five minutes):

```http
Authorization: Bearer <Django access JWT>
Content-Type: application/json
```

```json
{"id_token":"<fresh Google ID token>"}
```

Successful linking returns HTTP `200`:

```json
{"detail":"Google account linked successfully."}
```

Linking an already-linked identical Google subject returns HTTP `200` with
`{"detail":"Google account is already linked."}`. Invalid/missing authentication
returns HTTP `401`; an invalid ID token returns HTTP `401`; a conflicting or
already-owned Google subject returns HTTP `409`; verification unavailable
returns HTTP `503`. Linking changes only the Google subject; it does not
replace the account's email, password, role, or other profile
fields. Linking does not create, promote, or change a user's role.

## Django JWT session lifecycle

Google login returns a Django JWT pair, like email/password login. Use the access token for protected backend APIs:

```http
Authorization: Bearer <Django access JWT>
```

Access tokens last five minutes and refresh tokens last one day under the
current backend settings. Refresh with:

```http
POST /api/accounts/token/refresh/
Content-Type: application/json
```

```json
{"refresh":"<Django refresh JWT>"}
```

A successful refresh returns HTTP `200` with a new access token only:

```json
{"access":"<new Django access JWT>"}
```

Logout requires a valid access JWT and its corresponding refresh token:

```http
POST /api/accounts/logout/
Authorization: Bearer <Django access JWT>
Content-Type: application/json
```

```json
{"refresh":"<Django refresh JWT>"}
```

Success returns HTTP `205 Reset Content` with no response body. The refresh
token is blacklisted and cannot be used again. The backend does not rotate
refresh tokens on refresh.

The JWT login endpoint is `POST /api/accounts/login/` with
`{"email":"...","password":"..."}`; success returns `access` and `refresh`.
The response does not include `user_type`; call `GET /api/accounts/me/` with
the access JWT when the app needs the authenticated user's role.

## Roles and app routing

The supported `user_type` values are `customer`, `shop_admin`, and
`super_admin`. Route to the matching app experience based on the backend
account's `user_type`, not on a role supplied by the client. Backend endpoints
remain authoritative and enforce role permissions independently of UI
navigation. Google login creates only customers; an existing Shop Admin or
Super Admin can use Google only after that account has explicitly linked its
Google subject through an authenticated session.

## Firebase authentication and push-notification status

Firebase ID-token login and Firebase-token password setup have been removed. Existing users and the nullable `firebase_uid` database field are retained; email matching does not automatically link an account. Existing users must authenticate through an available Django login method, then explicitly link Google before using Google login for that account.

FCM push delivery has been disabled. In-app customer and Shop Admin notifications continue to be stored and exposed by the API, and legacy push-token records/endpoints remain for compatibility; no provider send is attempted. Firebase service-account files and historical user fields are not deleted by this change.

## Operational notes

- Both Google endpoints are throttled.
- Do not log ID tokens, JWTs, decoded token claims, or client secrets.
- Transmit ID tokens and JWTs only over HTTPS.
- Migration `accounts.0008_user_google_sub` adds the Google subject field and
  must be applied through the approved deployment process before rollout.
- Automated Google endpoint tests mock the verifier; they do not prove that
  live OAuth configuration or a real Google token works.
