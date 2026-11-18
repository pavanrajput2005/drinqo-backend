"""Post-implementation contract checks; synthetic accounts/provider proofs only."""
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .google_identity import GoogleIdentity
from .models import User


class FlutterAuthenticationContractTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def check_login_shape(self, response, provider):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"access", "refresh", "is_profile_complete", "user"})
        self.assertEqual(set(response.data["user"]), {
            "id", "email", "first_name", "last_name", "birth_date", "mobile_number",
            "auth_provider", "is_profile_complete", "user_type",
        })
        self.assertEqual(response.data["user"]["auth_provider"], provider)
        self.assertEqual(response.data["user"]["user_type"], "customer")
        self.assertIsNone(response.data["user"]["birth_date"])
        self.assertFalse(response.data["is_profile_complete"])

    @patch("accounts.mobile_views.verify_google_id_token", return_value=GoogleIdentity("contract-google-sub", "google@example.com"))
    def test_google_exact_login_shape(self, verifier):
        self.check_login_shape(self.client.post("/api/auth/google/", {"id_token": "synthetic-proof"}, format="json"), "google")

    @patch("accounts.mobile_views.verify_apple_identity", return_value=("contract-apple-sub", "apple@example.com"))
    def test_apple_exact_login_shape(self, verifier):
        self.check_login_shape(self.client.post("/api/auth/apple/", {"identity_token": "synthetic-proof"}, format="json"), "apple")

    def test_new_auth_validation_error_shapes(self):
        response = self.client.post("/api/auth/google/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"error": "id_token: This field is required."})
        response = self.client.post("/api/auth/verify-otp/", {"email": "none@example.com", "otp": "012345"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"error": "Invalid or expired OTP."})

    def test_registration_login_me_refresh_logout_contracts(self):
        body = {"username": "contract-user", "email": "contract@example.com", "password": "Strong-Synthetic-Password-732!"}
        response = self.client.post("/api/accounts/register/", body, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data, {"username": body["username"], "email": body["email"], "phone": ""})
        response = self.client.post("/api/accounts/login/", {"email": body["email"], "password": body["password"]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"refresh", "access"})
        refresh, access = response.data["refresh"], response.data["access"]
        response = self.client.post("/api/accounts/token/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"access"})
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
        response = self.client.get("/api/accounts/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"id", "firebase_uid", "email", "phone", "user_type", "is_verified", "age_verified", "age_self_declared", "age_self_declared_at", "date_of_birth", "age_verified_at"})
        response = self.client.post("/api/accounts/logout/", {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 205)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.client.post("/api/accounts/token/refresh/", {"refresh": refresh}, format="json").status_code, 401)

    @override_settings(PASSWORD_RESET_URL="")
    def test_reset_request_200_does_not_prove_delivery(self):
        User.objects.create_user(username="reset-user", email="reset@example.com", password="Strong-Synthetic-Password-732!")
        response = self.client.post("/api/accounts/password-reset/", {"email": "reset@example.com"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"detail": "If an account with that email is eligible, password reset instructions will be sent."})
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_non_numeric_uid_is_safe_bad_request(self):
        for raw_uid in ("not-a-user-id", "0", "-1", "9223372036854775808"):
            with self.subTest(uid_kind=raw_uid):
                response = self.client.post("/api/accounts/password-reset/confirm/", {
                    "uid": urlsafe_base64_encode(force_bytes(raw_uid)), "token": "invalid-proof",
                    "password": "Strong-Synthetic-Password-732!", "confirm_password": "Strong-Synthetic-Password-732!",
                }, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"detail": "Invalid or expired password reset link."})

    def test_logout_non_object_json_is_safe_bad_request(self):
        user = User.objects.create_user(username="logout-user", email="logout@example.com")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + str(RefreshToken.for_user(user).access_token))
        response = self.client.post("/api/accounts/logout/", [], format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"refresh": "This field is required."})
