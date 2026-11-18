from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import CustomerProfile, FavoriteShop, UserPushToken
from customers.serializers import CheckoutRequestSerializer
from shops.models import Shop, ShopLicenseDocument
from django.utils import timezone
from datetime import timedelta


class CustomerSecurityCorrectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="customer", email="customer@example.com", password="CustomerPassword#123"
        )
        self.other = User.objects.create_user(
            username="other", email="other@example.com", password="OtherPassword#123"
        )
        self.owner = User.objects.create_user(
            username="shop", email="shop@example.com", password="ShopPassword#123", user_type="shop_admin"
        )
        self.shop = Shop.objects.create(
            owner=self.owner, shop_name="Eligible shop", phone="9876543210",
            address_line1="1 Test Street", city="Pune", state="MH", postal_code="411001",
            license_number="LIC-1", license_status="verified", is_active=True,
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop, license_number="LIC-1", document="test.pdf",
            expiry_date=timezone.localdate() + timedelta(days=1),
            verification_status="verified", verified_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_favorite_post_is_idempotent_and_scoped_to_customer(self):
        payload = {"shop_id": self.shop.id}
        first = self.client.post(reverse("favorite-shop-list-create"), payload, format="json")
        second = self.client.post(reverse("favorite-shop-list-create"), payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(FavoriteShop.objects.filter(shop=self.shop).count(), 1)

    def test_push_token_cannot_be_taken_over_by_other_customer(self):
        payload = {"token": "device-token", "platform": "android"}
        self.assertEqual(
            self.client.post(reverse("customer-push-token"), payload, format="json").status_code,
            201,
        )
        self.assertEqual(
            self.client.post(reverse("customer-push-token"), payload, format="json").status_code,
            200,
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.post(reverse("customer-push-token"), payload, format="json").status_code,
            403,
        )
        self.assertEqual(UserPushToken.objects.get(token="device-token").user_id, self.user.id)

    def test_deletion_request_and_cancel_are_idempotent(self):
        first = self.client.post(reverse("account-deletion-request"))
        second = self.client.post(reverse("account-deletion-request"))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.client.post(reverse("account-deletion-request-cancel")).status_code, 200)
        self.assertEqual(self.client.post(reverse("account-deletion-request-cancel")).status_code, 200)

    def test_checkout_preserves_walk_in_and_requires_vehicle_for_default_mode(self):
        serializer = CheckoutRequestSerializer(data={
            "pickup_mode": "walk_in", "latitude": "18.5204", "longitude": "73.8567",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["pickup_mode"], "walk_in")
        self.assertNotIn("vehicle_id", serializer.validated_data)
        serializer = CheckoutRequestSerializer(data={
            "latitude": "18.5204", "longitude": "73.8567",
        })
        self.assertFalse(serializer.is_valid())
