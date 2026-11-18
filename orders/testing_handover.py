"""Test-only fixtures. Never import from application code or production settings."""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop, ShopLicenseDocument
from .models import Order

HANDOVER_TEST_LOCATION = {"latitude": "18.520430", "longitude": "73.856744"}


def staff_confirmation_payload(customer):
    return {
        **HANDOVER_TEST_LOCATION, "location_captured_at": timezone.now().isoformat(),
        "customer_id": customer.pk, "customer_checked": True, "order_checked": True,
        "physically_handed_over": True, "outside_shop_confirmed": True,
    }


class HandoverFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.handover_location = dict(HANDOVER_TEST_LOCATION)
        self.customer = User.objects.create_user(username="otp-customer", email="otp-customer@example.com", user_type="customer")
        self.owner = User.objects.create_user(username="otp-owner", email="otp-owner@example.com", user_type="shop_admin")
        self.other_owner = User.objects.create_user(username="otp-other", email="otp-other@example.com", user_type="shop_admin")
        self.admin = User.objects.create_user(username="otp-admin", email="otp-admin@example.com", user_type="super_admin")
        self.shop = Shop.objects.create(
            owner=self.owner, shop_name="OTP Shop", license_number="OTP-LIC", license_status="verified", is_active=True,
            **HANDOVER_TEST_LOCATION,
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop, license_number="OTP-LIC", document="private_shop_licenses/otp-test.pdf",
            verification_status="verified", expiry_date=timezone.localdate() + timedelta(days=365),
        )
        self.order = Order.objects.create(
            customer=self.customer, shop=self.shop, status="ready", pickup_mode="vehicle",
            subtotal="1000.00", application_fee="50.00", total_amount="1050.00",
        )
        # Handover security cases assume a separately captured payment.
        from orders.testing_payments import record_captured_payment_for_test
        record_captured_payment_for_test(self.order)
        self.client = APIClient()
