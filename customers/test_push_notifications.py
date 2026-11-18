from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import Notification
from shop_admin.models import ShopAdminNotification


class PushDeliveryDisabledTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="push-test-customer",
            email="push-test-customer@example.com",
            password="unit-test-password",
            user_type="customer",
        )
        self.shop_admin = User.objects.create_user(
            username="push-test-shop-admin",
            email="push-test-shop-admin@example.com",
            password="unit-test-password",
            user_type="shop_admin",
        )

    def test_customer_notification_persists_without_scheduling_push(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            notification = Notification.objects.create(
                user=self.customer,
                notification_type="order_update",
                title="Order Updated",
                message="Your order is ready.",
            )

        self.assertEqual(callbacks, [])
        self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())

    def test_shop_admin_notification_persists_without_scheduling_push(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            notification = ShopAdminNotification.objects.create(
                shop_admin=self.shop_admin,
                notification_type="new_order",
                title="New Order",
                message="A new order was placed.",
            )

        self.assertEqual(callbacks, [])
        self.assertTrue(
            ShopAdminNotification.objects.filter(pk=notification.pk).exists()
        )


class JWTRejectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jwt-rejection-user",
            email="jwt-rejection@example.com",
            password="unit-test-password",
        )
        self.client = APIClient()

    def test_invalid_and_expired_access_tokens_are_rejected(self):
        invalid_response = self.client.get(
            "/api/accounts/me/",
            HTTP_AUTHORIZATION="Bearer not-a-token",
        )
        self.assertEqual(invalid_response.status_code, 401)

        from rest_framework_simplejwt.tokens import AccessToken

        token = AccessToken.for_user(self.user)
        token["exp"] = int((timezone.now() - timedelta(seconds=1)).timestamp())
        expired_response = self.client.get(
            "/api/accounts/me/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(expired_response.status_code, 401)
