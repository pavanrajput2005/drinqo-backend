from datetime import date, timedelta
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .apple_identity import InvalidAppleIdentity, AppleVerificationUnavailable, verify_apple_identity
from .google_identity import GoogleIdentity, InvalidGoogleIdentity
from .models import EmailAuthState, EmailOTP, User


class MobileAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.email = "customer@example.com"

    def user(self, **kwargs):
        return User.objects.create_user(
            username="existing", email=self.email, password="Example-long-password-123", **kwargs,
        )

    def issue(self, age=0, code="012345"):
        record = EmailOTP(email=self.email)
        record.set_code(code)
        record.save()
        if age:
            EmailOTP.objects.filter(pk=record.pk).update(created_at=timezone.now() - timedelta(seconds=age))
        return record

    def verify(self, code="012345"):
        return self.client.post("/api/auth/verify-otp/", {"email": self.email, "otp": code}, format="json")

    def authenticate(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")

    def test_send_delivers_six_digits_and_normalizes_email(self):
        response = self.client.post("/api/auth/send-otp/", {"email": "CUSTOMER@example.com"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"message": "OTP sent successfully"})
        record = EmailOTP.objects.get(email=self.email)
        self.assertRegex(record.otp_code, r"^[0-9a-f]{64}$")
        import re
        delivered_code = re.search(r"code is ([0-9]{6})", mail.outbox[0].body).group(1)
        self.assertTrue(record.check_code(delivered_code))
        self.assertNotEqual(delivered_code, record.otp_code)
        self.assertNotIn(record.otp_code, str(response.data))

    def test_resend_rate_limit(self):
        body = {"email": self.email}
        self.assertEqual(self.client.post("/api/auth/send-otp/", body, format="json").status_code, 200)
        self.assertEqual(self.client.post("/api/auth/send-otp/", body, format="json").status_code, 429)
        self.assertEqual(EmailOTP.objects.count(), 1)

    def test_resend_replaces_older_code_without_resetting_attempt_budget(self):
        self.issue(code="111111")
        self.verify("999999")
        response = self.client.post("/api/auth/send-otp/", {"email": self.email}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailOTP.objects.count(), 1)
        self.assertEqual(EmailAuthState.objects.get().verify_count, 1)

    @patch("accounts.mobile_views.send_mail", side_effect=OSError("smtp password must never leak"))
    def test_failed_email_rolls_back_issuance(self, mocked):
        response = self.client.post("/api/auth/send-otp/", {"email": self.email}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("password", str(response.data))
        self.assertFalse(EmailOTP.objects.exists())

    def test_valid_otp_creates_customer_and_is_single_use(self):
        self.issue()
        response = self.verify()
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        refresh = RefreshToken(response.data["refresh"])
        user = User.objects.get(pk=refresh["user_id"])
        self.assertEqual(user.auth_provider, "email")
        self.assertEqual(user.user_type, "customer")
        self.assertTrue(user.email_verified)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(self.verify().status_code, 400)

    def test_existing_customer_is_reused(self):
        user = self.user(email_verified=True)
        self.issue()
        self.assertEqual(self.verify().data["user"]["id"], user.pk)
        self.assertEqual(User.objects.count(), 1)

    def test_mailbox_proof_does_not_adopt_unverified_password_account(self):
        user = self.user()
        self.issue()
        response = self.verify()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("refresh", response.data)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)
        self.assertFalse(EmailOTP.objects.exists())

    def test_wrong_otp_consumes_attempt(self):
        self.issue()
        self.assertEqual(self.verify("999999").status_code, 400)
        self.assertEqual(EmailAuthState.objects.get().verify_count, 1)

    def test_expired_and_future_otp_rejected(self):
        for seconds in (300, 301, -30):
            with self.subTest(seconds=seconds):
                EmailOTP.objects.all().delete()
                self.issue(age=seconds)
                self.assertEqual(self.verify().status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_attempt_budget_rejects_even_correct_code_after_limit(self):
        self.issue()
        for _ in range(10):
            self.assertEqual(self.verify("999999").status_code, 400)
        self.assertEqual(self.verify().status_code, 429)

    def test_blocked_inactive_and_admin_cannot_use_customer_otp(self):
        for fields in ({"is_blocked": True}, {"is_active": False}, {"user_type": "super_admin"}, {"user_type": "shop_admin"}):
            with self.subTest(fields=fields):
                user = self.user(**fields)
                self.issue()
                self.assertEqual(self.verify().status_code, 401)
                user.delete()

    def test_malformed_missing_and_invalid_fields_have_error_envelope(self):
        for body in ({}, {"email": "bad"}, {"email": self.email, "otp": 123456}, {"email": self.email, "otp": "12345"}, []):
            with self.subTest(body=body):
                response = self.client.post("/api/auth/verify-otp/", body, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(response.data), {"error"})
        response = self.client.post("/api/auth/send-otp/", '{"email":', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    @patch("accounts.mobile_views.verify_google_id_token")
    def test_google_creates_subject_bound_user(self, verify):
        verify.return_value = GoogleIdentity("google-sub", self.email, "Test", "Customer")
        response = self.client.post("/api/auth/google/", {"id_token": "trusted-test-proof"}, format="json")
        self.assertEqual(response.status_code, 200)
        user = User.objects.get()
        self.assertEqual((user.google_sub, user.auth_provider, user.first_name), ("google-sub", "google", "Test"))
        self.assertEqual(self.client.post("/api/auth/google/", {"id_token": "trusted-test-proof"}, format="json").status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    @patch("accounts.mobile_views.verify_google_id_token", side_effect=InvalidGoogleIdentity())
    def test_invalid_google_token(self, verify):
        response = self.client.post("/api/auth/google/", {"id_token": "bad"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(User.objects.exists())

    @patch("accounts.mobile_views.verify_google_id_token")
    def test_google_does_not_silently_link_existing_email(self, verify):
        self.user()
        verify.return_value = GoogleIdentity("different-sub", self.email)
        response = self.client.post("/api/auth/google/", {"id_token": "proof"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(User.objects.get().google_sub)

    @patch("accounts.mobile_views.verify_apple_identity")
    def test_apple_subject_login_without_repeat_email(self, verify):
        verify.return_value = ("apple-sub", self.email)
        body = {"identity_token": "proof", "first_name": "Customer"}
        self.assertEqual(self.client.post("/api/auth/apple/", body, format="json").status_code, 200)
        verify.return_value = ("apple-sub", None)
        response = self.client.post("/api/auth/apple/", {"identity_token": "new-proof"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().auth_provider, "apple")

    @patch("accounts.mobile_views.verify_apple_identity", return_value=("new-sub", None))
    def test_apple_without_email_cannot_create_fabricated_account(self, verify):
        response = self.client.post("/api/auth/apple/", {"identity_token": "proof"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(User.objects.exists())

    def test_profile_requires_authentication(self):
        response = self.client.patch("/api/user/profile/", {}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.data)

    def test_profile_syncs_existing_fields_and_resets_declaration(self):
        user = self.user(date_of_birth=date(1990, 1, 1), age_self_declared=True, age_self_declared_at=timezone.now())
        from customers.models import CustomerProfile
        profile = CustomerProfile.objects.create(
            user=user, full_name="Existing Customer", date_of_birth=user.date_of_birth,
            address_line1="Road", city="Mumbai", state="Maharashtra", postal_code="400001",
        )
        self.authenticate(user)
        response = self.client.patch("/api/user/profile/", {"birth_date": "1991-02-03", "mobile_number": "9876543210"}, format="json")
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.date_of_birth, user.birth_date)
        self.assertEqual(user.phone, user.mobile_number)
        self.assertTrue(user.is_profile_complete)
        self.assertFalse(user.age_self_declared)
        self.assertFalse(user.age_verified)
        profile.refresh_from_db()
        self.assertEqual(profile.date_of_birth, user.birth_date)
        self.assertEqual(profile.phone, user.mobile_number)

    def test_invalid_access_token_has_clean_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer forged-token")
        response = self.client.patch("/api/user/profile/", {}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(set(response.data), {"error"})

    @patch("accounts.mobile_views.verify_google_id_token")
    def test_social_login_rejects_blocked_subject(self, verify):
        self.user(google_sub="google-sub", is_blocked=True)
        verify.return_value = GoogleIdentity("google-sub", self.email)
        response = self.client.post("/api/auth/google/", {"id_token": "proof"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)

    def test_profile_rejects_future_date_invalid_mobile_and_privilege_fields(self):
        user = self.user()
        self.authenticate(user)
        for body in ({"birth_date": "2030-01-01", "mobile_number": "1234567890"}, {"birth_date": "1990/01/01"}, {"mobile_number": "123"}, {"mobile_number": "+919876543210"}, {"user_type": "super_admin"}, {}):
            with self.subTest(body=body):
                response = self.client.patch("/api/user/profile/", body, format="json")
                self.assertEqual(response.status_code, 400)

    def test_profile_cannot_replace_verified_dob(self):
        user = self.user(date_of_birth=date(1990, 1, 1), phone="9876543210", age_verified=True)
        self.authenticate(user)
        self.assertEqual(self.client.patch("/api/user/profile/", {"birth_date": "1991-01-01"}, format="json").status_code, 400)

    def test_legacy_updates_sync_mobile_fields(self):
        user = self.user()
        user.date_of_birth, user.phone = date(1990, 1, 1), "9876543210"
        user.save(update_fields=["date_of_birth", "phone"])
        user.refresh_from_db()
        self.assertEqual(user.birth_date, user.date_of_birth)
        self.assertEqual(user.mobile_number, user.phone)

    def test_case_insensitive_email_is_database_unique(self):
        self.user()
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(username="duplicate", email="CUSTOMER@example.com")])


class AppleSignatureTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def claims(self, **changes):
        now = int(timezone.now().timestamp())
        return {"iss": "https://appleid.apple.com", "aud": "test.drinqo.app", "sub": "apple-user", "iat": now, "exp": now + 300, "email": "apple@example.com", "email_verified": "true", **changes}

    def verify(self, claims, signing_key=None):
        token = jwt.encode(claims, signing_key or self.private, algorithm="RS256", headers={"kid": "test-key"})
        with patch("accounts.apple_identity.apple_keys") as keys:
            keys.return_value.get_signing_key_from_jwt.return_value.key = self.private.public_key()
            return verify_apple_identity(token)

    def test_valid_signed_token(self):
        self.assertEqual(self.verify(self.claims()), ("apple-user", "apple@example.com"))

    def test_wrong_audience_issuer_expiry_and_unverified_email(self):
        for changes in ({"aud": "attacker"}, {"iss": "https://attacker.invalid"}, {"exp": 1}, {"email_verified": "false"}, {"sub": ""}):
            with self.subTest(changes=changes), self.assertRaises(InvalidAppleIdentity):
                self.verify(self.claims(**changes))

    def test_signature_cannot_be_forged(self):
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(InvalidAppleIdentity):
            self.verify(self.claims(), attacker)

    @override_settings(APPLE_CLIENT_IDS=())
    def test_unconfigured_provider_fails_closed(self):
        with self.assertRaises(AppleVerificationUnavailable):
            verify_apple_identity("anything")
