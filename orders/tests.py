from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from orders.models import Order, OrderLiveLocation
from orders.serializers import OrderSerializer
from orders.testing_handover import staff_confirmation_payload
from customers.models import CustomerProfile, CustomerVehicle
from orders.testing_payments import record_captured_payment_for_test
from shops.models import Shop, ShopLicenseDocument


class OrderDefaultsTests(SimpleTestCase):
    def test_application_fee_default_matches_checkout_fee(self):
        field = Order._meta.get_field("application_fee")
        self.assertEqual(Decimal(str(field.get_default())), Decimal("50.00"))


class OrderLiveLocationApiTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="tracking-customer",
            email="tracking-customer@example.com",
            password="test-password",
            user_type="customer",
        )
        self.other_customer = User.objects.create_user(
            username="other-tracking-customer",
            email="other-tracking-customer@example.com",
            password="test-password",
            user_type="customer",
        )
        self.shop_admin = User.objects.create_user(
            username="tracking-shop-admin",
            email="tracking-shop-admin@example.com",
            password="test-password",
            user_type="shop_admin",
        )
        self.other_shop_admin = User.objects.create_user(
            username="other-tracking-shop-admin",
            email="other-tracking-shop-admin@example.com",
            password="test-password",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Tracking Shop",
            phone="9876543210",
            address_line1="1 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="TRACKING-LICENSE-1",
            license_status="verified",
            is_active=True,
            latitude="18.520430",
            longitude="73.856744",
        )
        self.license_document = ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/tracking-license.pdf",
            expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status="verified",
            verified_at=timezone.now(),
        )
        self.order = Order.objects.create(
            customer=self.customer,
            shop=self.shop,
            status="preparing",
            pickup_mode="vehicle",
            subtotal=Decimal("1000.00"),
            application_fee=Decimal("50.00"),
            total_amount=Decimal("1050.00"),
        )
        self.client = APIClient()
        self.url = reverse("order-live-location", args=[self.order.id])

    def test_shop_owner_can_start_and_customer_can_read_location(self):
        self.client.force_authenticate(self.shop_admin)
        publish = self.client.post(
            self.url,
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(publish.status_code, 200)
        self.assertEqual(
            set(publish.data),
            {"order_id", "is_sharing", "latitude", "longitude", "updated_at"},
        )
        self.assertTrue(publish.data["is_sharing"])
        self.assertEqual(publish.data["latitude"], 18.52043)
        self.assertEqual(publish.data["longitude"], 73.856744)
        self.assertIsNotNone(publish.data["updated_at"])

        self.client.force_authenticate(self.customer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_sharing"])
        self.assertEqual(response.data["latitude"], 18.52043)
        self.assertEqual(response.data["longitude"], 73.856744)
        self.assertIsNotNone(response.data["updated_at"])
        self.assertTrue(response.json()["updated_at"].endswith("Z"))
        self.assertEqual(
            set(response.data),
            {"order_id", "is_sharing", "latitude", "longitude", "updated_at"},
        )

    def test_closed_shop_allows_eligible_existing_order_location_and_pickup(self):
        self.shop.is_open = False
        self.shop.save(update_fields=["is_open"])
        self.order.status = "confirmed"
        self.order.vehicle = CustomerVehicle.objects.create(
            customer=CustomerProfile.objects.create(user=self.customer), vehicle_type="car", vehicle_number="TEST-01",
        )
        self.order.save(update_fields=["status", "vehicle"])
        record_captured_payment_for_test(self.order)
        self.client = APIClient()
        self.client.force_authenticate(self.shop_admin)

        preparing = self.client.post(
            reverse("shop_admin_update_order_status", args=[self.order.id]),
            {"status": "preparing"},
            format="json",
        )
        ready = self.client.post(
            reverse("shop_admin_update_order_status", args=[self.order.id]),
            {"status": "ready"},
            format="json",
        )
        location = self.client.post(
            reverse("order-live-location", args=[self.order.id]),
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        pickup = self.client.post(
            reverse("staff-confirm-handover", args=[self.order.id]),
            staff_confirmation_payload(self.customer),
            format="json",
        )

        self.assertEqual(preparing.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(location.status_code, 200)
        self.assertEqual(pickup.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "completed")
        self.assertTrue(self.order.pickup_verified)

    def test_unapproved_or_rejected_shop_cannot_publish_or_verify(self):
        self.order.status = "ready"
        self.order.save(update_fields=["status"])
        self.client = APIClient()
        self.client.force_authenticate(self.shop_admin)
        for license_status in ("pending", "rejected"):
            with self.subTest(license_status=license_status):
                self.shop.license_status = license_status
                self.shop.is_active = False
                self.shop.save(
                    update_fields=["license_status", "is_active"]
                )
                location = self.client.post(
                    reverse("order-live-location", args=[self.order.id]),
                    {
                        "latitude": "18.520430",
                        "longitude": "73.856744",
                        "is_sharing": True,
                    },
                    format="json",
                )
                pickup = self.client.post(
                    reverse("staff-confirm-handover", args=[self.order.id]),
                    staff_confirmation_payload(self.customer),
                    format="json",
                )
                self.assertEqual(location.status_code, 403)
                self.assertEqual(pickup.status_code, 403)
                self.assertFalse(OrderLiveLocation.objects.filter(
                    order=self.order,
                ).exists())
                self.order.refresh_from_db()
                self.assertEqual(self.order.status, "ready")

    def test_owning_shop_admin_can_read_location(self):
        OrderLiveLocation.objects.create(
            order=self.order,
            publisher=self.shop_admin,
            latitude=Decimal("18.520430"),
            longitude=Decimal("73.856744"),
        )

        self.client.force_authenticate(self.shop_admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_sharing"])
        self.assertEqual(response.data["latitude"], 18.52043)
        self.assertEqual(response.data["longitude"], 73.856744)

    def test_get_without_location_returns_null_coordinates(self):
        self.client.force_authenticate(self.customer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_sharing"])
        self.assertIsNone(response.data["latitude"])
        self.assertIsNone(response.data["longitude"])
        self.assertIsNone(response.data["updated_at"])

    def test_order_response_includes_checkout_and_vehicle_contract_fields(self):
        response = OrderSerializer(self.order).data

        self.assertEqual(response["order_number"], f"DRQ-{self.order.id:04d}")
        self.assertEqual(response["payment_method"], "online")
        self.assertEqual(response["application_fee"], "50.00")
        self.assertIsNone(response["vehicle_details"])

    def test_customer_order_reads_are_scoped_to_authenticated_customer(self):
        self.client.force_authenticate(self.other_customer)

        history = self.client.get(reverse("order-history"))
        detail = self.client.get(
            reverse("order-detail", args=[self.order.id])
        )
        payment_status = self.client.get(
            reverse("payment-status", args=[self.order.id])
        )
        tracking = self.client.get(
            reverse("order-tracking", args=[self.order.id])
        )

        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data["count"], 0)
        self.assertEqual(history.data["results"], [])
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(payment_status.status_code, 404)
        self.assertEqual(tracking.status_code, 404)

    def test_tracking_includes_accepted_state_and_reports_its_index(self):
        self.order.status = "accepted"
        self.order.save(update_fields=["status"])
        self.client.force_authenticate(self.customer)

        response = self.client.get(
            reverse("order-tracking", args=[self.order.id])
        )

        self.assertEqual(response.status_code, 200)
        accepted_index = next(
            index
            for index, step in enumerate(response.data["tracking_steps"])
            if step["status"] == "accepted"
        )
        self.assertEqual(response.data["current_step"], accepted_index)

    def test_cancel_conflict_uses_conflict_status(self):
        self.client.force_authenticate(self.customer)

        response = self.client.post(
            reverse("order-cancel", args=[self.order.id])
        )

        self.assertEqual(response.status_code, 409)

    def test_order_creation_rejects_non_customer_roles(self):
        self.shop_admin.age_verified = True
        self.shop_admin.save(update_fields=["age_verified"])
        self.client.force_authenticate(self.shop_admin)

        response = self.client.post(
            reverse("order-create"),
            {"pickup_mode": "walk_in"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "Customer access required.")

    def test_get_hides_coordinates_for_ineligible_order(self):
        OrderLiveLocation.objects.create(
            order=self.order,
            publisher=self.shop_admin,
            latitude=Decimal("18.520430"),
            longitude=Decimal("73.856744"),
        )
        self.order.status = "completed"
        self.order.save(update_fields=["status"])
        self.client.force_authenticate(self.customer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_sharing"])
        self.assertIsNone(response.data["latitude"])
        self.assertIsNone(response.data["longitude"])

    def test_location_is_hidden_from_other_customers_and_shop_admins(self):
        OrderLiveLocation.objects.create(
            order=self.order,
            publisher=self.shop_admin,
            latitude=Decimal("18.520430"),
            longitude=Decimal("73.856744"),
        )

        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.client.get(self.url).status_code, 404)

        self.client.force_authenticate(self.other_shop_admin)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(
            self.client.post(
                self.url,
                {
                    "latitude": "18.520430",
                    "longitude": "73.856744",
                    "is_sharing": True,
                },
                format="json",
            ).status_code,
            404,
        )

    def test_unauthenticated_requests_are_rejected(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

        response = self.client.post(
            self.url,
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_invalid_jwt_is_rejected(self):
        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION="Bearer not-a-django-jwt",
        )
        self.assertEqual(response.status_code, 401)

    def test_location_requires_vehicle_pickup_and_active_order_status(self):
        self.client.force_authenticate(self.shop_admin)
        self.order.pickup_mode = "walk_in"
        self.order.save(update_fields=["pickup_mode"])
        response = self.client.post(
            self.url,
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

        self.order.pickup_mode = "vehicle"
        self.order.status = "completed"
        self.order.save(update_fields=["pickup_mode", "status"])
        response = self.client.post(
            self.url,
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_coordinates_are_rejected(self):
        self.client.force_authenticate(self.shop_admin)
        invalid_payloads = (
            {
                "latitude": "91.000000",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            {
                "latitude": "18.520430",
                "longitude": "181.000000",
                "is_sharing": True,
            },
            {
                "latitude": "NaN",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            {
                "latitude": "Infinity",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            {
                "latitude": "18.520430",
                "longitude": "-Infinity",
                "is_sharing": True,
            },
            {
                "latitude": True,
                "longitude": "73.856744",
                "is_sharing": True,
            },
            {"latitude": "18.520430", "is_sharing": True},
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": False,
            },
            {"latitude": "18.520430", "longitude": "73.856744"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.url,
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

    def test_shop_owner_can_update_and_stop_location_sharing(self):
        self.client.force_authenticate(self.shop_admin)
        first = self.client.post(
            self.url,
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(first.status_code, 200)

        update = self.client.post(
            self.url,
            {
                "latitude": "19.076000",
                "longitude": "72.877700",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data["latitude"], 19.076)
        self.assertEqual(update.data["longitude"], 72.8777)

        stop = self.client.post(
            self.url,
            {"is_sharing": False},
            format="json",
        )
        self.assertEqual(stop.status_code, 200)
        self.assertFalse(stop.data["is_sharing"])
        self.assertIsNone(stop.data["latitude"])
        self.assertIsNone(stop.data["longitude"])
        self.assertIsNotNone(stop.data["updated_at"])
        self.assertEqual(
            set(stop.data),
            {"order_id", "is_sharing", "latitude", "longitude", "updated_at"},
        )

        location = OrderLiveLocation.objects.get(order=self.order)
        self.assertFalse(location.is_sharing)
        self.assertIsNone(location.latitude)
        self.assertIsNone(location.longitude)

    def test_stale_location_is_not_returned_to_customer(self):
        location = OrderLiveLocation.objects.create(
            order=self.order,
            publisher=self.shop_admin,
            latitude=Decimal("18.520430"),
            longitude=Decimal("73.856744"),
        )
        OrderLiveLocation.objects.filter(pk=location.pk).update(
            updated_at=timezone.now() - timedelta(seconds=61),
        )

        self.client.force_authenticate(self.customer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_sharing"])
        self.assertIsNone(response.data["latitude"])
        self.assertIsNone(response.data["longitude"])
        location.refresh_from_db()
        self.assertFalse(location.is_sharing)
        self.assertIsNone(location.latitude)
        self.assertIsNone(location.longitude)

    def test_stopping_or_completing_order_clears_shared_coordinates(self):
        location = OrderLiveLocation.objects.create(
            order=self.order,
            publisher=self.shop_admin,
            latitude=Decimal("18.520430"),
            longitude=Decimal("73.856744"),
        )
        self.client.force_authenticate(self.shop_admin)
        response = self.client.post(
            self.url,
            {"is_sharing": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_sharing"])
        self.assertIsNone(response.data["latitude"])
        self.assertIsNone(response.data["longitude"])
        location.refresh_from_db()
        self.assertFalse(location.is_sharing)
        self.assertIsNone(location.latitude)
        self.assertIsNone(location.longitude)

        location.is_sharing = True
        location.latitude = Decimal("18.520430")
        location.longitude = Decimal("73.856744")
        location.save()
        self.order.status = "completed"
        self.order.save(update_fields=["status"])

        location.refresh_from_db()
        self.assertFalse(location.is_sharing)
        self.assertIsNone(location.latitude)
        self.assertIsNone(location.longitude)

    def test_customer_cannot_publish_location(self):
        self.client.force_authenticate(self.customer)
        response = self.client.post(
            self.url,
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_unsupported_role_cannot_read_or_publish_location(self):
        super_admin = User.objects.create_user(
            username="tracking-super-admin",
            email="tracking-super-admin@example.com",
            password="tracking-test-password",
            user_type="super_admin",
        )
        self.client.force_authenticate(super_admin)

        self.assertEqual(self.client.get(self.url).status_code, 403)
        response = self.client.post(
            self.url,
            {"is_sharing": False},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
