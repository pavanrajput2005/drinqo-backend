# Flutter mobile authentication

The current implementation, exact API JSON, forward migrations, validation
results, provider configuration and Customer/Shop Flutter instructions are in
[Final authentication and outside-shop handover contract](final-auth-handover.md).

- Google: POST /api/auth/google/ with id_token.
- Apple: POST /api/auth/apple/ with identity_token and optional names.
- Email OTP: POST /api/auth/send-otp/, then POST /api/auth/verify-otp/.
- Email/password, refresh/logout, me and password recovery keep their existing
  /api/accounts/ contracts. Authenticated set-password is now supported.
- Phone/SMS OTP and Firebase phone exchange return 410. Existing phone-only
  users with a valid session can migrate a verified email without changing ID.
- Email codes are five-minute, single-use secrets stored as keyed digests.
- Order handover uses owning Shop Admin confirmation for Walk-in and Vehicle.
  Handover OTP issuance/reissue/verification and the old complete route return 410.
- Contact numbers remain editable; old verified-phone identity records remain
  retained and do not certify a changed contact number.

Provider token verification is separate from Django JWT sessions. Send-OTP never
returns JWTs before proof verification. This implementation has been tested in
isolated PostgreSQL; Apple, SMTP, Flutter and real-device workflows still require
configuration and live validation. The new forward migrations remain to be applied
to the shared deployment database during a coordinated rollout.
