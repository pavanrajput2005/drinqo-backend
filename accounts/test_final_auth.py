from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import re

from django.core import mail
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User, EmailOTP, VerifiedPhoneIdentity
from .mobile_views import locked_email_state


class FinalAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_retired_phone_routes_cannot_call_msg91_or_issue_tokens(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("Retired provider called")):
            for path in ("otp/send/", "otp/verify/", "phone-link/request/", "phone-link/verify/", "firebase-login/"):
                response = self.client.post("/api/accounts/" + path, {"phone": "+919876543210", "otp": "123456"}, format="json")
                self.assertEqual(response.status_code, 410)
                self.assertEqual(response.data["code"], "phone_otp_retired")
                self.assertNotIn("access", response.data)
                self.assertNotIn("refresh", response.data)
        self.assertFalse(User.objects.exists())

    def test_retirement_preserves_existing_records(self):
        user = User.objects.create_user(username="legacy", email="phone-9876543210@otp.drinqo.invalid", phone="+919876543210", firebase_uid="historical-firebase")
        identity = VerifiedPhoneIdentity.objects.create(user=user, phone_hash="a" * 64, phone_last4="3210")
        self.client.post("/api/accounts/otp/verify/", {"phone": user.phone, "otp": "123456"}, format="json")
        user.refresh_from_db()
        self.assertEqual(user.firebase_uid, "historical-firebase")
        self.assertTrue(VerifiedPhoneIdentity.objects.filter(pk=identity.pk).exists())

    def test_legacy_customer_can_link_email_without_changing_identity(self):
        user = User.objects.create_user(username="legacy", email="phone-9876543210@otp.drinqo.invalid", phone="+919876543210")
        VerifiedPhoneIdentity.objects.create(user=user, phone_hash="b" * 64, phone_last4="3210")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        body = {"email": "real@example.com"}
        response = self.client.post("/api/auth/link-email/send-otp/", body, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        code = re.search(r"code is ([0-9]{6})", mail.outbox[-1].body).group(1)
        # An email-link challenge is not a customer-login challenge.
        public = APIClient().post("/api/auth/verify-otp/", {**body, "otp": code}, format="json")
        self.assertEqual(public.status_code, 400)
        response = self.client.post("/api/auth/link-email/verify-otp/", {**body, "otp": code}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["id"], user.pk)
        user.refresh_from_db()
        self.assertEqual(user.email, body["email"])
        self.assertEqual(user.phone, "+919876543210")
        self.assertEqual(User.objects.count(), 1)

    def test_verified_email_account_can_set_password_and_revoke_refresh(self):
        user = User.objects.create_user(username="social", email="social@example.com", email_verified=True)
        user.set_unusable_password()
        user.save()
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        response = self.client.post("/api/accounts/set-password/", {"password": "Secure-Password-8234!", "confirm_password": "Secure-Password-8234!"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Secure-Password-8234!"))
        self.assertEqual(APIClient().post("/api/accounts/token/refresh/", {"refresh": str(refresh)}, format="json").status_code, 401)

    def test_existing_password_requires_current_password(self):
        user = User.objects.create_user(username="password", email="password@example.com", password="Existing-password-123!", email_verified=True)
        self.client.force_authenticate(user)
        response = self.client.post("/api/accounts/set-password/", {"password": "New-Password-8234!", "confirm_password": "New-Password-8234!"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_apple_invalid_and_unconfigured_errors_are_safe(self):
        from .apple_identity import InvalidAppleIdentity, AppleVerificationUnavailable
        for exception, status in ((InvalidAppleIdentity(), 401), (AppleVerificationUnavailable(), 503)):
            with patch("accounts.mobile_views.verify_apple_identity", side_effect=exception):
                response = self.client.post("/api/auth/apple/", {"identity_token": "private-proof"}, format="json")
                self.assertEqual(response.status_code, status)
                self.assertNotIn("private-proof", str(response.data))

    def test_blocked_admin_refresh_is_rejected(self):
        user = User.objects.create_user(username="blocked-admin", email="admin@example.com", user_type="super_admin", is_blocked=True)
        refresh = RefreshToken.for_user(user)
        response = self.client.post("/api/accounts/token/refresh/", {"refresh": str(refresh)}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_deleted_account_refresh_is_cleanly_rejected(self):
        user = User.objects.create_user(username="deleted", email="deleted@example.com")
        refresh = RefreshToken.for_user(user)
        user.delete()
        self.assertEqual(self.client.post("/api/accounts/token/refresh/", {"refresh": str(refresh)}, format="json").status_code, 401)

    def test_registration_email_collision_does_not_raise_server_error(self):
        User.objects.create_user(username="existing", email="collision@example.com")
        response = self.client.post("/api/accounts/register/", {"username": "new", "email": "COLLISION@example.com", "phone": "9876543210", "password": "Strong-new-password-123!"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), 1)


    def test_contact_number_can_change_without_rewriting_historical_phone_proof(self):
        from datetime import date
        from .contact_identity import phone_hash
        from .contact_identity import current_phone_verified
        user = User.objects.create_user(username="contact", email="contact@example.com", phone="+919876543210", date_of_birth=date(1990, 1, 1))
        identity = VerifiedPhoneIdentity.objects.create(user=user, phone_hash=phone_hash(user.phone), phone_last4="3210")
        self.assertTrue(current_phone_verified(user))
        self.client.force_authenticate(user)
        response = self.client.patch("/api/user/profile/", {"mobile_number": "9123456789"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        self.assertEqual(user.phone, "9123456789")
        self.assertFalse(current_phone_verified(user))
        identity.refresh_from_db()
        self.assertEqual(identity.phone_last4, "3210")


class EmailOTPConcurrencyTests(TransactionTestCase):
    def test_concurrent_verification_issues_only_one_token_pair(self):
        self.assertEqual(connection.vendor, "postgresql", "Concurrency acceptance requires PostgreSQL")
        record = EmailOTP(email="race@example.com")
        record.set_code("000123")
        record.save()
        # Create the shared throttling anchor before the workers contend.
        from django.db import transaction
        with transaction.atomic():
            locked_email_state(record.email)
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = APIClient().post("/api/auth/verify-otp/", {"email": record.email, "otp": "000123"}, format="json")
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            codes = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(codes), [200, 400])
        self.assertEqual(User.objects.filter(email=record.email).count(), 1)
