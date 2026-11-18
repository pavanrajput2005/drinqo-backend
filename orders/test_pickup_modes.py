"""Outside-shop pickup contracts. Run only against an isolated test database."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from customers.models import Cart, CartItem, CustomerProfile, CustomerVehicle
from orders.models import Order
from orders.testing_handover import HandoverFixture, staff_confirmation_payload
from orders.testing_payments import record_captured_payment_for_test
from products.models import Product


class PickupModeTests(HandoverFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.customer.phone = "9876543210"
        self.customer.date_of_birth = date(1990, 1, 1)
        self.customer.age_self_declared = True
        self.customer.age_self_declared_at = timezone.now()
        self.customer.save()
        self.profile = CustomerProfile.objects.create(
            user=self.customer, full_name="Pickup customer", address_line1="Test street",
            city="Pune", state="Maharashtra", postal_code="411001",
        )
        self.vehicle = CustomerVehicle.objects.create(
            customer=self.profile, vehicle_type="car", vehicle_number="PICKUP-TEST",
        )
        self.shop.latitude = Decimal("18.520430")
        self.shop.longitude = Decimal("73.856744")
        self.shop.save()
        self.product = Product.objects.create(
            shop=self.shop, name="Pickup product", category="other",
            price="500.00", stock_quantity=30,
        )
        self.cart = Cart.objects.create(customer=self.profile)
        self.refill()
        self.client.force_authenticate(self.customer)

    def refill(self):
        CartItem.objects.update_or_create(
            cart=self.cart, product=self.product, defaults={"quantity": 1},
        )

    def payload(self, mode):
        result = {
            "pickup_mode": mode, "latitude": "18.520430", "longitude": "73.856744",
        }
        if mode == "vehicle":
            result["vehicle_id"] = self.vehicle.id
        return result

    def checkout(self, mode, preview=False, **changes):
        self.client.force_authenticate(self.customer)
        return self.client.post(
            reverse("checkout-preview" if preview else "order-create"),
            {**self.payload(mode), **changes}, format="json",
        )

    def test_both_modes_preserved_with_server_prices_and_one_fee(self):
        for mode in ("walk_in", "vehicle"):
            with self.subTest(mode=mode):
                self.refill()
                preview = self.checkout(mode, preview=True)
                self.assertEqual(preview.status_code, 200, preview.data)
                self.assertTrue(preview.data["can_checkout"])
                self.assertEqual(preview.data["pickup_mode"], mode)
                self.assertEqual(preview.data["subtotal"], "500.00")
                self.assertEqual(preview.data["application_fee"], "50.00")
                self.assertEqual(preview.data["platform_fee"], "50.00")
                self.assertEqual(preview.data["total_amount"], "550.00")
                if mode == "walk_in":
                    self.assertIsNone(preview.data["vehicle"])
                else:
                    self.assertEqual(preview.data["vehicle"]["id"], self.vehicle.id)
                created = self.checkout(
                    mode, shop_id=999999, subtotal="1.00", total_amount="1.00",
                    application_fee="0.00", payment_status="paid",
                )
                self.assertEqual(created.status_code, 201, created.data)
                order = Order.objects.get(pk=created.data["id"])
                self.assertEqual(order.pickup_mode, mode)
                self.assertEqual(order.vehicle_id, self.vehicle.id if mode == "vehicle" else None)
                self.assertEqual(order.shop_id, self.shop.id)
                self.assertEqual(order.total_amount, Decimal("550.00"))
                self.assertEqual(order.payment_status, "pending")
                self.assertEqual(order.items.get().unit_price, Decimal("500.00"))
                self.assertFalse(self.cart.items.exists())
                self.client.force_authenticate(self.owner)
                detail = self.client.get(f"/api/shop-admin/orders/{order.pk}/")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.data["pickup_mode"], mode)
                self.assertEqual(detail.data["vehicle"], order.vehicle_id)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 28)

    def test_vehicle_required_owned_and_walk_in_rejects_conflicting_vehicle(self):
        other = User.objects.create_user(username="vehicle-other", user_type="customer")
        profile = CustomerProfile.objects.create(user=other, full_name="Other")
        foreign = CustomerVehicle.objects.create(
            customer=profile, vehicle_type="bike", vehicle_number="FOREIGN-PICKUP",
        )
        for endpoint in ("checkout-preview", "order-create"):
            for vehicle_id in (None, 999999, foreign.id):
                with self.subTest(endpoint=endpoint, vehicle_id=vehicle_id):
                    payload = self.payload("vehicle")
                    if vehicle_id is None:
                        del payload["vehicle_id"]
                    else:
                        payload["vehicle_id"] = vehicle_id
                    response = self.client.post(reverse(endpoint), payload, format="json")
                    self.assertEqual(response.status_code, 400)
            conflict = self.client.post(
                reverse(endpoint), {**self.payload("walk_in"), "vehicle_id": self.vehicle.id},
                format="json",
            )
            self.assertEqual(conflict.status_code, 400)
            self.assertIn("vehicle_id", conflict.data["errors"])

    def test_both_modes_geofence_missing_and_invalid_coordinates(self):
        for mode in ("walk_in", "vehicle"):
            for endpoint in ("checkout-preview", "order-create"):
                for missing in ("latitude", "longitude"):
                    with self.subTest(mode=mode, endpoint=endpoint, missing=missing):
                        payload = self.payload(mode)
                        del payload[missing]
                        response = self.client.post(reverse(endpoint), payload, format="json")
                        self.assertEqual(response.status_code, 400)
                for invalid in (True, "NaN", "Infinity", "91", None):
                    response = self.client.post(
                        reverse(endpoint), {**self.payload(mode), "latitude": invalid}, format="json",
                    )
                    self.assertEqual(response.status_code, 400)
            # About 50m away passes, about 200m away fails. No frontend distance is used.
            inside = self.checkout(mode, preview=True, latitude="18.520880")
            self.assertTrue(inside.data["can_checkout"])
            self.assertLess(inside.data["distance_to_shop_m"], 100)
            outside = self.checkout(mode, preview=True, latitude="18.522230")
            self.assertEqual(outside.status_code, 200)
            self.assertFalse(outside.data["can_checkout"])
            self.assertGreater(outside.data["distance_to_shop_m"], 100)
            denied = self.checkout(mode, latitude="18.522230")
            self.assertEqual(denied.status_code, 400)
            self.assertEqual(denied.data["pickup_radius_m"], 100)
            self.assertEqual(self.checkout(mode, latitude="18.520880").status_code, 201)
            self.refill()

    def test_both_modes_reject_missing_shop_location(self):
        self.shop.latitude = None
        self.shop.save()
        for mode in ("walk_in", "vehicle"):
            preview = self.checkout(mode, preview=True)
            self.assertEqual(preview.status_code, 200)
            self.assertFalse(preview.data["can_checkout"])
            self.assertIsNone(preview.data["distance_to_shop_m"])
            self.assertEqual(self.checkout(mode).status_code, 400)

    def test_both_modes_shop_and_product_availability(self):
        for field, bad in (("is_active", False), ("is_open", False),
                           ("license_status", "pending"), ("is_suspended_by_super_admin", True)):
            original = getattr(self.shop, field)
            setattr(self.shop, field, bad)
            self.shop.save()
            for mode in ("walk_in", "vehicle"):
                self.assertEqual(self.checkout(mode, preview=True).status_code, 400)
                self.assertEqual(self.checkout(mode).status_code, 400)
            setattr(self.shop, field, original)
            self.shop.save()
        document = self.shop.application_documents.get()
        document.expiry_date = timezone.localdate()
        document.save()
        for mode in ("walk_in", "vehicle"):
            self.assertEqual(self.checkout(mode, preview=True).status_code, 400)
            self.assertEqual(self.checkout(mode).status_code, 400)
        document.expiry_date = timezone.localdate() + timedelta(days=365)
        document.save()
        for field, bad in (("is_active", False), ("stock_quantity", 0)):
            original = getattr(self.product, field)
            setattr(self.product, field, bad)
            self.product.save()
            for mode in ("walk_in", "vehicle"):
                self.assertEqual(self.checkout(mode, preview=True).status_code, 400)
                self.assertEqual(self.checkout(mode).status_code, 400)
            setattr(self.product, field, original)
            self.product.save()

    def test_minimum_and_inclusive_total_approval_threshold_for_both_modes(self):
        for mode in ("walk_in", "vehicle"):
            for price, expected in (("499.99", None), ("14949.99", "pending"),
                                    ("14950.00", "pending_super_admin")):
                self.refill()
                self.product.price = Decimal(price)
                self.product.save()
                preview = self.checkout(mode, preview=True)
                self.assertEqual(preview.data["can_checkout"], expected is not None)
                created = self.checkout(mode)
                self.assertEqual(created.status_code, 201 if expected else 400)
                if expected:
                    self.assertEqual(created.data["status"], expected)
                    self.assertEqual(Decimal(created.data["total_amount"]), Decimal(price) + 50)
                    if expected == "pending_super_admin":
                        self.client.force_authenticate(self.owner)
                        result = self.client.post(
                            f"/api/shop-admin/orders/{created.data['id']}/decision/",
                            {"decision": "accepted"}, format="json",
                        )
                        self.assertEqual(result.status_code, 409)

    def test_both_modes_paid_state_flow_and_staff_completion(self):
        for mode in ("walk_in", "vehicle"):
            self.refill()
            created = self.checkout(mode)
            self.assertEqual(created.status_code, 201)
            order = Order.objects.get(pk=created.data["id"])
            self.client.force_authenticate(self.owner)
            accepted = self.client.post(
                f"/api/shop-admin/orders/{order.pk}/decision/", {"decision": "accepted"}, format="json",
            )
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.data["status"], "confirmed")
            if mode == "walk_in":
                self.assertIn("outside the shop within 100 metres", accepted.data["customer_message"])
                self.assertNotIn("vehicle", accepted.data["customer_message"])
            status_url = f"/api/shop-admin/orders/{order.pk}/status/"
            blocked = self.client.post(status_url, {"status": "preparing"}, format="json")
            self.assertEqual(blocked.status_code, 402)
            record_captured_payment_for_test(order)
            self.assertEqual(self.client.post(status_url, {"status": "preparing"}, format="json").status_code, 200)
            self.assertEqual(self.client.post(status_url, {"status": "ready"}, format="json").status_code, 200)
            confirmed = self.client.post(reverse("staff-confirm-handover", args=[order.pk]),
                                         staff_confirmation_payload(self.customer), format="json")
            self.assertEqual(confirmed.status_code, 200)
            order.refresh_from_db()
            self.assertTrue(order.pickup_verified)
            self.assertEqual(order.pickup_verified_by_id, self.owner.pk)
