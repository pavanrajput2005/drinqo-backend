# DRINQO customer API handoff — six issue correction

This is source-derived API documentation, not proof of live Flutter/provider integration. All paths are relative to the API origin and JSON requests use `Content-Type: application/json`.

## 1. Customer registration

**Confirmed result:** no server registration defect was found. Registration creates a `customer`, securely hashes its password, and deliberately does **not** issue tokens. Flutter must call login after a `201`.

- **POST** `/api/accounts/register/`; no authentication.
- Required request: `{"username":"customer_name","email":"customer@example.invalid","phone":"+919876543210","password":"A-strong-password"}`.
- **201:** `{"username":"customer_name","email":"customer@example.invalid","phone":"+919876543210"}`.
- **400:** DRF field-keyed errors, e.g. `{"email":["user with this email already exists."]}` or `{"password":["…password validation message…"]}`.
- Email is unique. Raw `phone` intentionally is not a login identity or database-unique claim; ownership is established only by the OTP phone-link workflow, preventing editable legacy-phone account hijacking.
- Follow with **POST** `/api/accounts/login/`, body `{"email":"…","password":"…"}`; **200** `{"refresh":"<JWT>","access":"<JWT>"}`.

Source: `accounts/models.py:5-42`, `accounts/serializers.py:118-141`, `accounts/views.py:77-85`, `accounts/urls.py:22-30`.

## 2. Google sign-in

- **POST** `/api/accounts/google-login/`; no authentication.
- Request: `{"id_token":"<Google OpenID Connect ID token>"}`.
- **200:** `{"id":123,"email":"customer@example.invalid","phone":"","user_type":"customer","is_verified":false,"age_verified":false,"refresh":"<JWT>","access":"<JWT>"}`.
- **400:** missing/malformed request field. **401:** `{"detail":"Unable to authenticate with Google."}` for invalid identity, disabled/blocked account, or unlinked same-email collision. **503:** same safe detail when configuration/certificate retrieval is unavailable.

The server verifies Google signature, expiry, issuer, configured audience, configured Android authorized-party ID, `sub`, and `email_verified`. A new valid identity creates an active customer with unusable password; an existing Google subject keeps its existing account/role. Same-email accounts are never silently linked.

Deployment prerequisite: configure public client IDs in `GOOGLE_OAUTH_CLIENT_IDS` and, for Android `azp`, `GOOGLE_OAUTH_ANDROID_CLIENT_IDS`. Runtime values were not inspected.

Source: `accounts/google_identity.py:90-180`, `accounts/views.py:625-751`, `config/settings.py:86-129`.

## 3. Password reset

- **POST** `/api/accounts/password-reset/`; no authentication; request `{"email":"customer@example.invalid"}`.
- **200 for every syntactically valid email:** `{"detail":"If an account with that email is eligible, password reset instructions will be sent."}`. This avoids account enumeration.
- Generated link: `<PASSWORD_RESET_URL>?uid=<url-safe-user-id>&token=<Django-reset-token>`.
- **POST** `/api/accounts/password-reset/confirm/`; no authentication; request `{"uid":"<uid>","token":"<token>","password":"New strong password","confirm_password":"New strong password"}`.
- **200:** `{"detail":"Password reset successfully."}`. **400 invalid/expired/reused token:** `{"detail":"Invalid or expired password reset link."}`; password/confirmation validation is field-keyed 400.

Reset uses Django's time-limited single-use token and atomically blacklists outstanding refresh JWTs. Mail is sent only when `PASSWORD_RESET_URL` and `DEFAULT_FROM_EMAIL` are configured; live delivery is **NOT VERIFIED**.

Source: `accounts/views.py:442-543`, `accounts/serializers.py:55-85`, `config/settings.py:382-408`, `accounts/tests.py:367-542`.

## 4. Customer vehicles

- **GET / POST** `/api/customer/vehicles/`; **GET / PATCH / DELETE** `/api/customer/vehicles/{id}/`; customer JWT required.
- POST request: `{"vehicle_type":"car","vehicle_number":"MH12AB1234","vehicle_name":"My car"}`.
- Canonical case-sensitive enum: `car`, `bike`, `scooter`, `other`. UI labels such as `Car` are invalid.
- **201/200 object:** `{"id":42,"vehicle_type":"car","vehicle_number":"MH12AB1234","vehicle_name":"My car","created_at":"2026-11-16T00:00:00Z","updated_at":"2026-11-16T00:00:00Z"}`.
- **400:** field-keyed error for invalid enum or globally duplicate `vehicle_number`; **401** invalid/missing JWT; **403** non-customer; **404** non-owned/missing vehicle.

No server model/serializer mismatch exists. The integration must send the lower-case values.

Source: `customers/models.py:68-103`, `customers/serializers.py:73-88`, `customers/views.py:136-171`, `customers/urls.py:31-34`.

## 5. Link Google account

- **POST** `/api/accounts/google-link/`; authenticated current Django access JWT required.
- Request: `{"id_token":"<fresh Google ID token>"}`.
- **200:** `{"detail":"Google account linked successfully."}`; same already-linked subject returns `{"detail":"Google account is already linked."}`.
- **401:** invalid/missing JWT or Google token: `{"detail":"Unable to link Google account."}`. **403:** ineligible account, same safe detail. **409:** different existing link or subject belongs to another user, same safe detail. **503:** `{"detail":"Google verification is temporarily unavailable."}`.

The link token must pass all Google checks and be issued within five minutes. Only `google_sub` of the authenticated account changes; no auto-linking by email occurs.

Source: `accounts/views.py:754-828`, `accounts/google_identity.py:163-172`, `accounts/models.py:16-21`.

## 6. Profile and checkout

- **GET / PATCH** `/api/customer/profile/`; customer JWT required. Writable fields: `full_name`, `date_of_birth` (`YYYY-MM-DD`), `phone`, `email`, `address_line1`, `address_line2`, `city`, `state`, `postal_code`.
- A profile DOB is now synchronized to authoritative `accounts.User.date_of_birth`, the value checkout checks. A DOB change clears self-declaration and non-documentary verification flags; declaration must then be made again. DOB cannot change after approved age proof.
- **POST** `/api/customer/checkout/preview/` and **POST** `/api/orders/create/`; customer JWT required.
- Request: `{"pickup_mode":"vehicle","vehicle_id":42,"latitude":"18.520430","longitude":"73.856744"}`. Vehicle is the only pickup mode; finite decimal latitude/longitude and an owned vehicle are required.
- Shared eligibility failure example: `{"detail":"Complete your personal details before checkout.","code":"profile_incomplete","missing_fields":["state"]}`.
- Other codes: `date_of_birth_required`, `underage_customer`, `age_self_declaration_required` (403); profile incomplete is 400.

Checkout requires full name, profile-or-account phone, address line 1, city, state, postal code, age 21+, and persisted `age_self_declared` or legacy authoritative `age_verified`. Self-declaration does not set `age_verified`.

Preview 200 contains calculated `subtotal`, `application_fee`, alias `platform_fee`, `delivery_fee`, `total_amount`, `minimum_order_amount`, `can_checkout`, shop/items/vehicle/geofence data. Create 201 returns the persisted `OrderSerializer` object. Product subtotal minimum stays INR 500.00; distinct platform/application fee INR 50.00; total INR 15,000+ preserves Super Admin approval, with stock/shop/geofence/vehicle-handover protections unchanged.

Source: `customers/views.py:74-133,512-749`, `customers/checkout_eligibility.py:13-80`, `customers/serializers.py:264-348`, `orders/views.py:67-145`, `orders/serializers.py:29-72`.

## Validation and blockers

Focused source tests added: successful registration/password hashing; all canonical vehicle types plus invalid type; profile DOB synchronization and profile completion. Existing tests cover Google identity/linking security, reset token single-use/refresh revocation, and structured checkout eligibility states.

Database-backed tests have **not** run as part of this handoff; run them only through the verified isolated PostgreSQL workflow, never the development database. No migration was created or applied in this six-issue batch.

Before live integration, configure Google client IDs, configure/reset-mail delivery, and ensure Flutter uses the deployed Django API origin and Django JWTs (not Firebase tokens) for protected requests.
