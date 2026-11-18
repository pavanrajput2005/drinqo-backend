from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import re
import uuid
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core import mail
from django.db import close_old_connections
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from accounts.google_identity import (
    GoogleIdentity,
    GoogleVerificationUnavailable,
    InvalidGoogleIdentity,
    verify_google_id_token,
)
from customers.models import Notification
from orders.models import Order
from shops.models import Shop


GOOGLE_TEST_CLIENT_ID = "123456789-test.apps.googleusercontent.com"
GOOGLE_TEST_CLIENT_ID_2 = "987654321-test.apps.googleusercontent.com"
GOOGLE_TEST_ANDROID_CLIENT_ID = "456789123-test.apps.googleusercontent.com"


class FirebaseAuthRemovalTests(SimpleTestCase):
    def test_firebase_login_is_retired_and_password_setup_requires_auth(self):
        client = APIClient()
        self.assertEqual(client.post(reverse("retired-firebase-login")).status_code, 410)
        self.assertEqual(client.post(reverse("set-account-password")).status_code, 401)

    def test_google_oauth_and_jwt_endpoints_remain_routed(self):
        expected_paths = {
            "google-login": "/api/accounts/google-login/",
            "login": "/api/accounts/login/",
            "token_refresh": "/api/accounts/token/refresh/",
            "logout": "/api/accounts/logout/",
        }

        for route_name, expected_path in expected_paths.items():
            with self.subTest(route_name=route_name):
                self.assertEqual(reverse(route_name), expected_path)


class JWTAuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.password = "StrongTestPassword#123"
        self.user = User.objects.create_user(
            username="linked-customer",
            email="linked@example.com",
            password=self.password,
            phone="+919876543210",
            firebase_uid="firebase-linked-user",
            user_type="customer",
            is_verified=True,
        )

    def obtain_tokens(self, user=None):
        user = user or self.user
        response = self.client.post(
            reverse("login"),
            {"email": user.email, "password": self.password},
            format="json",
        )
        return response

    def test_jwt_login_me_and_refresh(self):
        response = self.obtain_tokens()

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}"
        )
        me_response = self.client.get(reverse("me"))
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data["id"], self.user.id)
        self.assertEqual(me_response.data["user_type"], "customer")
        self.assertEqual(
            me_response.data["firebase_uid"],
            "firebase-linked-user",
        )

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200)
        self.assertIn("access", refresh_response.data)

    def test_password_login_is_limited_per_ip_without_email_enumeration(self):
        with patch(
            "rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES",
            {"password_login": "5/minute"},
        ):
            known_email_failure = self.client.post(
                reverse("login"),
                {
                    "email": self.user.email,
                    "password": "incorrect-password",
                },
                format="json",
                REMOTE_ADDR="192.0.2.10",
            )
            unknown_email_failure = self.client.post(
                reverse("login"),
                {
                    "email": "not-registered@example.com",
                    "password": "incorrect-password",
                },
                format="json",
                REMOTE_ADDR="192.0.2.10",
            )
            for _ in range(3):
                response = self.client.post(
                    reverse("login"),
                    {
                        "email": "not-registered@example.com",
                        "password": "incorrect-password",
                    },
                    format="json",
                    REMOTE_ADDR="192.0.2.10",
                )
                self.assertEqual(response.status_code, 401)

            limited_response = self.client.post(
                reverse("login"),
                {
                    "email": "not-registered@example.com",
                    "password": "incorrect-password",
                },
                format="json",
                REMOTE_ADDR="192.0.2.10",
            )
            other_ip_response = self.client.post(
                reverse("login"),
                {
                    "email": "not-registered@example.com",
                    "password": "incorrect-password",
                },
                format="json",
                REMOTE_ADDR="192.0.2.11",
            )

        self.assertEqual(known_email_failure.status_code, 401)
        self.assertEqual(unknown_email_failure.status_code, 401)
        self.assertEqual(known_email_failure.data, unknown_email_failure.data)
        self.assertEqual(limited_response.status_code, 429)
        self.assertEqual(other_ip_response.status_code, 401)

    def test_password_login_fails_closed_when_throttle_cache_errors(self):
        with patch(
            "rest_framework.throttling.ScopedRateThrottle.allow_request",
            side_effect=RuntimeError("cache unavailable"),
        ):
            response = self.client.post(
                reverse("login"),
                {
                    "email": self.user.email,
                    "password": self.password,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data["detail"],
            "Authentication is temporarily unavailable.",
        )

    def test_customer_with_blocked_status_cannot_obtain_jwt(self):
        self.user.is_blocked = True
        self.user.save(update_fields=["is_blocked"])

        response = self.obtain_tokens()

        self.assertEqual(response.status_code, 403)

    def test_profile_and_age_verification_accept_jwt(self):
        response = self.obtain_tokens()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}"
        )

        profile_response = self.client.get(reverse("profile"))
        age_response = self.client.post(
            reverse("age_verification"),
            {"date_of_birth": "2000-01-01"},
            format="json",
        )

        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(age_response.status_code, 200)
        self.assertFalse(age_response.data["age_verified"])
        self.assertEqual(
            age_response.data["age_verification_status"],
            "proof_required",
        )

    def test_age_verification_rejects_non_string_malformed_and_impossible_dates(self):
        response = self.obtain_tokens()
        self.client.credentials(
            HTTP_AUTHORIZATION="Bearer " + response.data["access"]
        )
        invalid_payloads = (
            {},
            {"date_of_birth": None},
            {"date_of_birth": 20000101},
            {"date_of_birth": True},
            {"date_of_birth": "2000-1-01"},
            {"date_of_birth": "20000101"},
            {"date_of_birth": "2000-02-30"},
            {"date_of_birth": "9999-12-31"},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    reverse("age_verification"),
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("date_of_birth", response.data)


@override_settings(
    MAILERS={
        "default": {
            "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
        }
    },
    DEFAULT_FROM_EMAIL="no-reply@example.com",
    PASSWORD_RESET_URL="drinqo://reset-password",
    PASSWORD_RESET_TIMEOUT=3600,
)
class PasswordRecoveryTests(TestCase):
    generic_success_detail = (
        "If an account with that email is eligible, password reset "
        "instructions will be sent."
    )

    def setUp(self):
        throttle_rate_patcher = patch(
            "rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES",
            {
                "password_reset_request": "1000/hour",
                "password_reset_confirm": "1000/hour",
                # The reset test logs in before the reset. Keep that endpoint
                # configured rather than turning the fail-closed throttle into
                # an unrelated 503 during fixture setup.
                "password_login": "1000/hour",
            },
        )
        throttle_rate_patcher.start()
        self.addCleanup(throttle_rate_patcher.stop)
        cache.clear()
        mail.mailers["default"]
        mail.outbox.clear()
        self.client = APIClient()
        self.password = "OriginalCustomerPassword#123"
        self.user = User.objects.create_user(
            username="recovery-customer",
            email="recovery@example.com",
            phone="+919876543210",
            password=self.password,
            user_type="customer",
            is_verified=True,
            email_verified=True,
        )
        self.shop_admin = User.objects.create_user(
            username="recovery-shop-admin",
            email="recovery-shop@example.com",
            password="ShopAdminPassword#123",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Recovery Test Shop",
            phone="9876543210",
            address_line1="1 Test Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="RECOVERY-TEST-SHOP",
        )
        self.order = Order.objects.create(
            customer=self.user,
            shop=self.shop,
            subtotal="100.00",
            total_amount="150.00",
        )
        self.notification = Notification.objects.create(
            user=self.user,
            title="Existing account notification",
            message="Preserve this notification",
        )

    def request_reset(self, email=None):
        return self.client.post(
            reverse("password-reset"),
            {"email": email or self.user.email},
            format="json",
        )

    def reset_link_values(self):
        link = re.search(
            r"drinqo://reset-password\?([^\s]+)",
            mail.outbox[-1].body,
        ).group(0)
        query = parse_qs(urlsplit(link).query)
        return query["uid"][0], query["token"][0]

    def confirm_reset(self, uid, token, password="FreshCustomerPassword#456"):
        return self.client.post(
            reverse("password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "password": password,
                "confirm_password": password,
            },
            format="json",
        )

    def test_verified_account_receives_generic_reset_request_success(self):
        response = self.request_reset()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "detail": (
                    "If an account with that email is eligible, password "
                    "reset instructions will be sent."
                )
            },
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("drinqo://reset-password?uid=", mail.outbox[0].body)

    def test_unknown_email_has_same_generic_response(self):
        response = self.request_reset("unknown@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["detail"], self.generic_success_detail)
        self.assertEqual(mail.outbox, [])

    def test_password_account_can_recover_through_its_registered_email(self):
        self.user.email_verified = False
        self.user.is_verified = True
        self.user.save(update_fields=["email_verified", "is_verified"])

        response = self.request_reset()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["detail"],
            self.generic_success_detail,
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_email_verified_defaults_false_without_changing_is_verified(self):
        user = User.objects.create_user(
            username="default-unverified",
            email="default-unverified@example.com",
            password="AnotherPassword#123",
            is_verified=True,
        )

        self.assertFalse(user.email_verified)
        self.assertTrue(user.is_verified)

    def test_invalid_token_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.confirm_reset(uid, "invalid-token")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {"detail": "Invalid or expired password reset link."},
        )

    def test_expired_token_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        expired_now = datetime.now() + timedelta(seconds=3601)

        with patch.object(
            default_token_generator,
            "_now",
            return_value=expired_now,
        ):
            response = self.confirm_reset(uid, token)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {"detail": "Invalid or expired password reset link."},
        )

    def test_weak_password_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        response = self.client.post(
            reverse("password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "password": "password",
                "confirm_password": "password",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_reset_is_single_use_invalidates_refresh_and_preserves_data(self):
        login_response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        access_token = login_response.data["access"]
        refresh_token = login_response.data["refresh"]

        request_response = self.request_reset()
        self.assertEqual(request_response.status_code, 200)
        uid, token = self.reset_link_values()
        original_user_id = self.user.pk
        original_shop_id = self.shop.pk
        original_order_status = self.order.status
        original_notification_message = self.notification.message

        response = self.confirm_reset(uid, token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"detail": "Password reset successfully."})
        self.user.refresh_from_db()
        self.shop.refresh_from_db()
        self.order.refresh_from_db()
        self.notification.refresh_from_db()
        self.assertEqual(self.user.pk, original_user_id)
        self.assertEqual(self.user.user_type, "customer")
        self.assertEqual(self.user.email, "recovery@example.com")
        self.assertEqual(self.user.phone, "+919876543210")
        self.assertTrue(self.user.is_verified)
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.shop.pk, original_shop_id)
        self.assertEqual(self.shop.owner_id, self.shop_admin.pk)
        self.assertEqual(self.order.status, original_order_status)
        self.assertEqual(self.order.customer_id, self.user.pk)
        self.assertEqual(self.order.shop_id, self.shop.pk)
        self.assertEqual(
            self.notification.message,
            original_notification_message,
        )

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": refresh_token},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 401)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        self.assertEqual(self.client.get(reverse("me")).status_code, 200)

        reuse_response = self.confirm_reset(uid, token)
        self.assertEqual(reuse_response.status_code, 400)

        new_login = self.client.post(
            reverse("login"),
            {
                "email": self.user.email,
                "password": "FreshCustomerPassword#456",
            },
            format="json",
        )
        self.assertEqual(new_login.status_code, 200)
        self.assertIn("access", new_login.data)

    def test_request_endpoint_is_rate_limited(self):
        cache.clear()
        with patch(
            "rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES",
            {
                "password_reset_request": "5/hour",
                "password_reset_confirm": "1000/hour",
            },
        ):
            responses = [self.request_reset() for _ in range(6)]

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 200, 200, 200, 429],
        )


class HTTPLogPrivacyTests(SimpleTestCase):
    def test_http_client_log_filter_redacts_query_parameters(self):
        import logging

        from accounts.http_log_privacy import _RedactHttpQueryStrings

        record = logging.LogRecord(
            "urllib3.connectionpool",
            logging.DEBUG,
            __file__,
            1,
            '"GET /api/v5/otp/verify?mobile=919876543210&otp=987321 HTTP/1.1"',
            (),
            None,
        )
        self.assertTrue(_RedactHttpQueryStrings().filter(record))
        message = record.getMessage()
        self.assertIn("?[REDACTED]", message)
        self.assertNotIn("987321", message)
        self.assertNotIn("919876543210", message)


class LogoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "StrongTestPassword#123"
        self.user = User.objects.create_user(
            username="logout-customer",
            email="logout@example.com",
            password=self.password,
        )
        self.other_user = User.objects.create_user(
            username="other-logout-customer",
            email="other-logout@example.com",
            password=self.password,
        )

    def issue_tokens(self, user):
        response = self.client.post(
            reverse("login"),
            {"email": user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_logout_blacklists_the_authenticated_users_refresh_token(self):
        tokens = self.issue_tokens(self.user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {tokens['access']}"
        )

        logout_response = self.client.post(
            reverse("logout"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(logout_response.status_code, 205)

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_logout_rejects_another_users_refresh_token(self):
        own_tokens = self.issue_tokens(self.user)
        other_tokens = self.issue_tokens(self.other_user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {own_tokens['access']}"
        )

        response = self.client.post(
            reverse("logout"),
            {"refresh": other_tokens["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, 403)


@override_settings(
    GOOGLE_OAUTH_CLIENT_IDS=(GOOGLE_TEST_CLIENT_ID, GOOGLE_TEST_CLIENT_ID_2),
    GOOGLE_OAUTH_CLIENT_IDS_CONFIG_VALID=True,
    GOOGLE_OAUTH_ANDROID_CLIENT_IDS=(GOOGLE_TEST_ANDROID_CLIENT_ID,),
    GOOGLE_OAUTH_ANDROID_CLIENT_IDS_CONFIG_VALID=True,
)
class GoogleIdentityVerificationTests(SimpleTestCase):
    def claims(self, **overrides):
        claims = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_TEST_CLIENT_ID,
            "sub": "stable-google-subject",
            "email": "Verified.User@example.com",
            "email_verified": True,
        }
        claims.update(overrides)
        return claims

    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_valid_token_uses_google_auth_and_returns_normalized_identity(
        self,
        mock_verify,
    ):
        mock_verify.return_value = self.claims(aud=GOOGLE_TEST_CLIENT_ID_2)

        identity = verify_google_id_token("mock-google-id-token")

        self.assertEqual(
            identity,
            GoogleIdentity(
                subject="stable-google-subject",
                email="verified.user@example.com",
            ),
        )
        self.assertIsNone(mock_verify.call_args.kwargs["audience"])

    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_multi_audience_requires_allowed_authorized_party(self, mock_verify):
        mock_verify.return_value = self.claims(
            aud=[GOOGLE_TEST_CLIENT_ID, GOOGLE_TEST_CLIENT_ID_2],
            azp=GOOGLE_TEST_CLIENT_ID_2,
        )
        self.assertEqual(
            verify_google_id_token("mock-google-id-token").subject,
            "stable-google-subject",
        )

        mock_verify.return_value = self.claims(
            aud=[GOOGLE_TEST_CLIENT_ID, GOOGLE_TEST_CLIENT_ID_2],
            azp="unapproved-client.apps.googleusercontent.com",
        )
        with self.assertRaises(InvalidGoogleIdentity):
            verify_google_id_token("mock-google-id-token")

    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_web_audience_accepts_explicitly_trusted_android_authorized_party(
        self,
        mock_verify,
    ):
        mock_verify.return_value = self.claims(
            aud=GOOGLE_TEST_CLIENT_ID,
            azp=GOOGLE_TEST_ANDROID_CLIENT_ID,
        )

        identity = verify_google_id_token("mock-google-id-token")

        self.assertEqual(identity.subject, "stable-google-subject")

        mock_verify.return_value = self.claims(
            aud=GOOGLE_TEST_CLIENT_ID,
            azp="untrusted-android.apps.googleusercontent.com",
        )
        with self.assertRaises(InvalidGoogleIdentity) as raised:
            verify_google_id_token("mock-google-id-token")
        self.assertEqual(raised.exception.reason_code, "authorized_party_mismatch")

        mock_verify.return_value = self.claims(
            aud="untrusted-web.apps.googleusercontent.com",
            azp=GOOGLE_TEST_ANDROID_CLIENT_ID,
        )
        with self.assertRaises(InvalidGoogleIdentity) as raised:
            verify_google_id_token("mock-google-id-token")
        self.assertEqual(raised.exception.reason_code, "audience_mismatch")

    @override_settings(
        GOOGLE_OAUTH_ANDROID_CLIENT_IDS=("not-an-oauth-client-id",),
        GOOGLE_OAUTH_ANDROID_CLIENT_IDS_CONFIG_VALID=False,
    )
    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_malformed_android_allowlist_does_not_trust_android_authorized_party(
        self,
        mock_verify,
    ):
        mock_verify.return_value = self.claims(
            aud=GOOGLE_TEST_CLIENT_ID,
            azp=GOOGLE_TEST_ANDROID_CLIENT_ID,
        )

        with self.assertRaises(InvalidGoogleIdentity) as raised:
            verify_google_id_token("mock-google-id-token")

        self.assertEqual(raised.exception.reason_code, "authorized_party_mismatch")

    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_account_linking_requires_a_recent_google_id_token(self, mock_verify):
        mock_verify.return_value = self.claims(iat=int(timezone.now().timestamp()))
        self.assertEqual(
            verify_google_id_token(
                "mock-google-id-token",
                require_fresh=True,
            ).subject,
            "stable-google-subject",
        )

        stale_iat = int(timezone.now().timestamp()) - 301
        for claims in (
            self.claims(iat=stale_iat),
            self.claims(iat=int(timezone.now().timestamp()) + 61),
            self.claims(),
        ):
            with self.subTest(iat=claims.get("iat")):
                mock_verify.return_value = claims
                with self.assertRaises(InvalidGoogleIdentity):
                    verify_google_id_token(
                        "mock-google-id-token",
                        require_fresh=True,
                    )

    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_invalid_identity_claims_are_rejected(self, mock_verify):
        invalid_claims = (
            {"iss": "https://attacker.example"},
            {"aud": "unapproved-client.apps.googleusercontent.com"},
            {"sub": ""},
            {"sub": "x" * 256},
            {"sub": "not-ascii-\u2603"},
            {"email_verified": False},
            {"email": "not-an-email"},
            {"email": ""},
        )
        for changes in invalid_claims:
            with self.subTest(changes=changes):
                mock_verify.return_value = self.claims(**changes)
                with self.assertRaises(InvalidGoogleIdentity):
                    verify_google_id_token("mock-google-id-token")

    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_invalid_or_expired_signed_token_is_rejected(self, mock_verify):
        from google.auth.exceptions import InvalidValue, MalformedError

        mock_verify.side_effect = InvalidValue("sensitive expiration details")
        with self.assertRaises(InvalidGoogleIdentity) as expired:
            verify_google_id_token("mock-google-id-token")
        self.assertEqual(expired.exception.reason_code, "token_expiry_or_time_invalid")
        self.assertNotIn("sensitive expiration details", str(expired.exception))

        mock_verify.side_effect = MalformedError("sensitive signature details")
        with self.assertRaises(InvalidGoogleIdentity) as invalid_signature:
            verify_google_id_token("mock-google-id-token")
        self.assertEqual(
            invalid_signature.exception.reason_code,
            "token_signature_or_format_invalid",
        )
        self.assertNotIn("sensitive signature details", str(invalid_signature.exception))

    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_google_verification_transport_failure_fails_closed(self, mock_verify):
        from google.auth.exceptions import TransportError

        mock_verify.side_effect = TransportError("sensitive request details")
        with self.assertRaises(GoogleVerificationUnavailable):
            verify_google_id_token("mock-google-id-token")

    @override_settings(
        GOOGLE_OAUTH_CLIENT_IDS=(),
        GOOGLE_OAUTH_CLIENT_IDS_CONFIG_VALID=False,
    )
    @patch("accounts.google_identity.google_id_token.verify_oauth2_token")
    def test_missing_client_configuration_never_calls_verifier(self, mock_verify):
        with self.assertRaises(GoogleVerificationUnavailable):
            verify_google_id_token("mock-google-id-token")
        mock_verify.assert_not_called()


@override_settings(
    GOOGLE_OAUTH_CLIENT_IDS=(GOOGLE_TEST_CLIENT_ID, GOOGLE_TEST_CLIENT_ID_2),
    GOOGLE_OAUTH_CLIENT_IDS_CONFIG_VALID=True,
    REST_FRAMEWORK={
        "DEFAULT_THROTTLE_RATES": {
            "google_login": "1000/hour",
            "google_link": "1000/hour",
        },
    },
)
class GoogleAuthenticationTests(TestCase):
    raw_token = "mock-google-id-token"

    def setUp(self):
        self.client = APIClient()

    def identity(self, subject="stable-google-subject", email="google@example.com"):
        return GoogleIdentity(subject=subject, email=email)

    def user(self, **overrides):
        options = {
            "username": f"user-{uuid.uuid4().hex}",
            "email": f"user-{uuid.uuid4().hex}@example.com",
            "password": "ExistingStrongPassword#123",
            "user_type": "customer",
        }
        options.update(overrides)
        return User.objects.create_user(**options)

    def google_login(self, identity=None, extra_data=None):
        data = {"id_token": self.raw_token}
        if extra_data:
            data.update(extra_data)
        with patch(
            "accounts.views.verify_google_id_token",
            return_value=identity or self.identity(),
        ):
            return self.client.post(
                reverse("google-login"),
                data,
                format="json",
            )

    def authenticate_as(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}"
        )

    def google_link(self, identity=None):
        with patch(
            "accounts.views.verify_google_id_token",
            return_value=identity or self.identity(),
        ):
            return self.client.post(
                reverse("google-link"),
                {"id_token": self.raw_token},
                format="json",
            )

    def test_existing_linked_user_receives_jwt_without_subject_disclosure(self):
        user = self.user(
            username="google-linked-customer",
            email="stored@example.com",
            google_sub="stable-google-subject",
            phone="+919876543210",
            is_verified=True,
        )

        response = self.google_login(
            self.identity(email="changed-google-email@example.com")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["email"], user.email)
        self.assertEqual(response.data["phone"], user.phone)
        self.assertEqual(response.data["user_type"], "customer")
        self.assertNotIn("google_sub", response.data)
        self.assertNotIn("id_token", response.data)
        self.assertNotIn(self.raw_token, str(response.data))

    def test_existing_linked_admin_roles_receive_jwt_without_role_changes(self):
        shop_admin = self.user(
            username="google-linked-shop-admin",
            email="google-shop-admin@example.com",
            google_sub="shop-admin-google-subject",
            user_type="shop_admin",
        )
        super_admin = self.user(
            username="google-linked-super-admin",
            email="google-super-admin@example.com",
            google_sub="super-admin-google-subject",
            user_type="super_admin",
        )

        for user, subject, expected_role, endpoint in (
            (shop_admin, "shop-admin-google-subject", "shop_admin", None),
            (
                super_admin,
                "super-admin-google-subject",
                "super_admin",
                "super-admin-me",
            ),
        ):
            with self.subTest(role=expected_role):
                response = self.google_login(
                    self.identity(subject=subject, email=user.email)
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["id"], user.id)
                self.assertEqual(response.data["user_type"], expected_role)
                self.assertIn("access", response.data)
                self.assertIn("refresh", response.data)
                user.refresh_from_db()
                self.assertEqual(user.user_type, expected_role)
                if endpoint:
                    self.client.credentials(
                        HTTP_AUTHORIZATION=f"Bearer {response.data['access']}"
                    )
                    self.assertEqual(
                        self.client.get(reverse(endpoint)).status_code,
                        200,
                    )
                    self.client.credentials()

    def test_new_verified_identity_creates_customer_with_unusable_password(self):
        response = self.google_login(
            extra_data={"email": "attacker@example.com", "user_type": "super_admin"}
        )

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(google_sub="stable-google-subject")
        self.assertEqual(user.email, "google@example.com")
        self.assertTrue(user.email_verified)
        self.assertEqual(user.user_type, "customer")
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(User.objects.count(), 1)

    def test_google_tokens_support_refresh_and_logout_revocation(self):
        login_response = self.google_login()
        self.assertEqual(login_response.status_code, 200)

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200)
        self.assertEqual(set(refresh_response.data), {"access"})

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}"
        )
        logout_response = self.client.post(
            reverse("logout"),
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(logout_response.status_code, 205)

        revoked_refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(revoked_refresh_response.status_code, 401)

    def test_existing_email_collision_is_not_linked_or_authenticated(self):
        existing = self.user(email="google@example.com", google_sub=None)

        response = self.google_login()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, {"detail": "Unable to authenticate with Google."})
        existing.refresh_from_db()
        self.assertIsNone(existing.google_sub)
        self.assertEqual(User.objects.count(), 1)

    def test_blocked_and_inactive_linked_users_are_rejected(self):
        for change in ({"is_blocked": True}, {"is_active": False}):
            with self.subTest(change=change):
                User.objects.all().delete()
                user = self.user(
                    google_sub="stable-google-subject",
                    **change,
                )
                response = self.google_login()
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data, {"detail": "Unable to authenticate with Google."})
                self.assertEqual(User.objects.count(), 1)
                user.refresh_from_db()
                self.assertEqual(user.google_sub, "stable-google-subject")

    def test_link_requires_an_authenticated_django_jwt(self):
        with patch("accounts.views.verify_google_id_token") as mock_verify:
            response = self.google_link()
        self.assertEqual(response.status_code, 401)
        mock_verify.assert_not_called()

    def test_authenticated_link_changes_only_google_subject(self):
        user = self.user(
            email="keep-this@example.com",
            phone="+919876543210",
            firebase_uid="preserved-firebase-uid",
            user_type="shop_admin",
            is_verified=True,
            email_verified=False,
            age_verified=True,
        )
        old_password = user.password
        self.authenticate_as(user)

        response = self.google_link()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {"detail": "Google account linked successfully."},
        )
        user.refresh_from_db()
        self.assertEqual(user.google_sub, "stable-google-subject")
        self.assertEqual(user.email, "keep-this@example.com")
        self.assertEqual(user.phone, "+919876543210")
        self.assertEqual(user.user_type, "shop_admin")
        self.assertTrue(user.is_verified)
        self.assertFalse(user.email_verified)
        self.assertTrue(user.age_verified)
        self.assertEqual(user.firebase_uid, "preserved-firebase-uid")
        self.assertEqual(user.password, old_password)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_account_link_verifier_requires_fresh_token(self):
        user = self.user()
        self.authenticate_as(user)
        with patch(
            "accounts.views.verify_google_id_token",
            return_value=self.identity(),
        ) as verifier:
            response = self.client.post(
                reverse("google-link"),
                {"id_token": self.raw_token},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        verifier.assert_called_once_with(self.raw_token, require_fresh=True)

    def test_link_rejects_replacement_and_subject_owned_by_another_user(self):
        current = self.user(google_sub="first-google-subject")
        other = self.user(google_sub="stable-google-subject")
        self.authenticate_as(current)

        replacement = self.google_link(self.identity(subject="second-google-subject"))
        already_owned = self.google_link()

        self.assertEqual(replacement.status_code, 409)
        self.assertEqual(already_owned.status_code, 409)
        current.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(current.google_sub, "first-google-subject")
        self.assertEqual(other.google_sub, "stable-google-subject")

    def test_same_identity_link_is_idempotent(self):
        user = self.user(google_sub="stable-google-subject")
        self.authenticate_as(user)

        response = self.google_link()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"detail": "Google account is already linked."})

    def test_google_verifier_failures_return_safe_responses_without_logging_token(self):
        with patch(
            "accounts.views.verify_google_id_token",
            side_effect=InvalidGoogleIdentity,
        ):
            with self.assertLogs(level="WARNING") as logs:
                response = self.client.post(
                    reverse("google-login"),
                    {"id_token": self.raw_token},
                    format="json",
                )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(self.raw_token, str(response.data))
        self.assertNotIn(self.raw_token, " ".join(logs.output))

        with patch(
            "accounts.views.verify_google_id_token",
            side_effect=GoogleVerificationUnavailable,
        ):
            response = self.client.post(
                reverse("google-login"),
                {"id_token": self.raw_token},
                format="json",
            )
        self.assertEqual(response.status_code, 503)

    def test_google_login_has_a_dedicated_rate_limit_scope(self):
        from django.core.cache import cache

        cache.clear()
        with patch(
            "rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES",
            {"google_login": "1/hour", "google_link": "1/hour"},
        ), patch(
            "accounts.views.verify_google_id_token",
            return_value=self.identity(),
        ):
            first = self.client.post(
                reverse("google-login"),
                {"id_token": self.raw_token},
                format="json",
            )
            second = self.client.post(
                reverse("google-login"),
                {"id_token": self.raw_token},
                format="json",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)


@override_settings(
    GOOGLE_OAUTH_CLIENT_IDS=(GOOGLE_TEST_CLIENT_ID,),
    GOOGLE_OAUTH_CLIENT_IDS_CONFIG_VALID=True,
    REST_FRAMEWORK={
        "DEFAULT_THROTTLE_RATES": {
            "google_login": "1000/hour",
            "google_link": "1000/hour",
        },
    },
)
class GoogleAuthenticationConcurrencyTests(TransactionTestCase):
    def test_concurrent_first_login_creates_one_user_and_both_can_authenticate(self):
        identity = GoogleIdentity(
            subject="concurrent-google-subject",
            email="concurrent-google@example.com",
        )
        barrier = Barrier(2)

        def wait_for_both_verifications(_token, **_kwargs):
            barrier.wait(timeout=10)
            return identity

        def login_request():
            close_old_connections()
            try:
                response = APIClient().post(
                    reverse("google-login"),
                    {"id_token": "mock-google-id-token"},
                    format="json",
                )
                return response.status_code, response.data.get("detail")
            finally:
                close_old_connections()

        with patch(
            "accounts.views.verify_google_id_token",
            side_effect=wait_for_both_verifications,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                statuses = list(executor.map(lambda _index: login_request(), range(2)))

        self.assertEqual([status for status, _detail in statuses], [200, 200], statuses)
        self.assertEqual(
            User.objects.filter(google_sub="concurrent-google-subject").count(),
            1,
        )

    def test_concurrent_linking_of_same_subject_has_one_owner(self):
        users = [
            User.objects.create_user(
                username=f"google-link-{index}",
                email=f"google-link-{index}@example.com",
                password="ExistingStrongPassword#123",
            )
            for index in range(2)
        ]
        identity = GoogleIdentity(
            subject="concurrent-link-subject",
            email="linked-google@example.com",
        )
        barrier = Barrier(2)

        def wait_for_both_verifications(_token, **_kwargs):
            barrier.wait(timeout=10)
            return identity

        def link_request(user):
            close_old_connections()
            try:
                refresh = RefreshToken.for_user(
                    User.objects.get(pk=user.pk)
                )
                client = APIClient()
                client.credentials(
                    HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}"
                )
                return client.post(
                    reverse("google-link"),
                    {"id_token": "mock-google-id-token"},
                    format="json",
                ).status_code
            finally:
                close_old_connections()

        with patch(
            "accounts.views.verify_google_id_token",
            side_effect=wait_for_both_verifications,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                statuses = list(executor.map(link_request, users))

        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(
            User.objects.filter(google_sub="concurrent-link-subject").count(),
            1,
        )
