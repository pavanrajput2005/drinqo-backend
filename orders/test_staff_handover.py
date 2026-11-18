from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from customers.models import CustomerProfile, CustomerVehicle, Notification
from products.models import Product
from shops.models import Shop, ShopLicenseDocument
from .models import Order, StaffHandoverConfirmation, CustomerHandoverLocation
from .testing_payments import record_captured_payment_for_test


class StaffHandoverFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.customer = User.objects.create_user(username="customer", email="customer@example.com")
        self.owner = User.objects.create_user(username="owner", email="owner@example.com", user_type="shop_admin")
        self.other = User.objects.create_user(username="other", email="other@example.com", user_type="shop_admin")
        self.shop = Shop.objects.create(owner=self.owner, shop_name="Licensed shop", license_number="STAFF-1", license_status="verified", is_active=True, latitude="18.520430", longitude="73.856744")
        ShopLicenseDocument.objects.create(shop=self.shop, license_number="STAFF-1", document="retained/test.pdf", verification_status="verified", expiry_date=timezone.localdate() + timedelta(days=365))
        self.profile = CustomerProfile.objects.create(user=self.customer, full_name="Customer", address_line1="Road", city="Pune", state="Maharashtra", postal_code="411001")
        self.vehicle = CustomerVehicle.objects.create(customer=self.profile, vehicle_type="car", vehicle_number="TEST-01")
        self.order = Order.objects.create(customer=self.customer, shop=self.shop, status="ready", pickup_mode="walk_in", subtotal="500.00", application_fee="50.00", total_amount="550.00")
        self.payment = record_captured_payment_for_test(self.order)
        self.product = Product.objects.create(shop=self.shop, name="Stock unchanged", category="other", price="500.00", stock_quantity=8)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f"/api/shop-admin/orders/{self.order.pk}/confirm-handover/"

    def payload(self, **changes):
        return {"customer_id": self.customer.pk, "customer_checked": True, "order_checked": True,
                "physically_handed_over": True, "outside_shop_confirmed": True,
                "latitude": "18.520430", "longitude": "73.856744",
                "location_captured_at": timezone.now().isoformat(), **changes}

    def confirm(self, **changes):
        return self.client.post(self.url, self.payload(**changes), format="json")


class StaffHandoverTests(StaffHandoverFixture, TestCase):
    def test_staff_jwt_rechecked_after_block_or_deactivation(self):
        token = AccessToken.for_user(self.owner)
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.owner.is_blocked = True
        self.owner.save(update_fields=["is_blocked"])
        self.assertEqual(self.confirm().status_code, 403)
        self.owner.is_blocked, self.owner.is_active = False, False
        self.owner.save(update_fields=["is_blocked", "is_active"])
        self.assertEqual(self.confirm().status_code, 401)
        self.assertFalse(StaffHandoverConfirmation.objects.exists())

    def test_completion_rolls_back_on_audit_or_notification_failure(self):
        for model in (StaffHandoverConfirmation, Notification):
            with self.subTest(model=model), patch.object(model.objects, "create", side_effect=RuntimeError("test rollback")):
                with self.assertRaisesRegex(RuntimeError, "test rollback"):
                    self.confirm()
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, "ready")
            self.assertFalse(self.order.pickup_verified)
            self.assertFalse(StaffHandoverConfirmation.objects.exists())
            self.assertFalse(Notification.objects.filter(order=self.order).exists())

    def test_reported_radius_is_inclusive(self):
        with patch("orders.staff_handover.distance_between_coordinates", return_value=100.001):
            self.assertEqual(self.confirm().status_code, 400)
        with patch("orders.staff_handover.distance_between_coordinates", return_value=100):
            self.assertEqual(self.confirm().status_code, 200)

    def test_historical_accepted_order_can_progress_without_challenge(self):
        self.order.status = "accepted"
        self.order.save(update_fields=["status"])
        response = self.client.post(f"/api/shop-admin/orders/{self.order.pk}/status/", {"status": "preparing"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "preparing")

    def test_walk_in_completion_audit_and_idempotent_replay(self):
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.data)
        record = StaffHandoverConfirmation.objects.get(order=self.order)
        self.assertEqual(record.actor_id, self.owner.pk)
        self.assertEqual(record.pickup_mode, "walk_in")
        self.assertIsNotNone(record.confirmed_at)
        self.assertTrue(record.acknowledgments["physically_handed_over"])
        self.assertFalse(response.data["already_completed"])
        repeated = self.confirm()
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.data["already_completed"])
        self.assertEqual(StaffHandoverConfirmation.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(order=self.order).count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)

    def test_vehicle_completion(self):
        self.order.pickup_mode, self.order.vehicle = "vehicle", self.vehicle
        self.order.save()
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(StaffHandoverConfirmation.objects.get().pickup_mode, "vehicle")

    def test_foreign_shop_customer_and_unauthenticated_rejected(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.confirm().status_code, 404)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.confirm().status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.confirm().status_code, 401)

    def test_nonready_and_terminal_rejected(self):
        for state in ("pending", "pending_super_admin", "confirmed", "preparing", "cancelled", "rejected", "completed"):
            self.order.status = state
            self.order.save()
            self.assertEqual(self.confirm().status_code, 409)
        self.assertFalse(StaffHandoverConfirmation.objects.exists())

    def test_unpaid_manual_paid_refunded_and_ledger_mismatch_rejected(self):
        for changes in ({"status": "created"}, {"status": "captured", "amount": 55001, "shop_amount": 50001}, {"amount": 55000, "shop_amount": 50000, "refunded_amount": 1}):
            for key, val in changes.items():
                setattr(self.payment, key, val)
            self.payment.save()
            self.assertEqual(self.confirm().status_code, 402)

    def test_high_value_fee_boundary_requires_approval(self):
        self.order.subtotal, self.order.total_amount = Decimal("14950.00"), Decimal("15000.00")
        self.order.save()
        record_captured_payment_for_test(self.order)
        self.assertEqual(self.confirm().status_code, 403)
        admin = User.objects.create_user(username="super", email="super@example.com", user_type="super_admin")
        self.order.super_admin_approved_at, self.order.super_admin_approved_by = timezone.now(), admin
        self.order.save()
        self.assertEqual(self.confirm().status_code, 200)

    def test_restricted_shop_without_review_rejected(self):
        self.shop.is_suspended_by_super_admin = True
        self.shop.save()
        self.assertEqual(self.confirm().status_code, 403)

    def test_expired_license_rejected(self):
        self.shop.application_documents.update(expiry_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.confirm().status_code, 403)

    def test_wrong_customer_missing_acknowledgment_and_forged_status_rejected(self):
        self.assertEqual(self.confirm(customer_id=self.other.pk).status_code, 400)
        for field in ("customer_checked", "order_checked", "physically_handed_over", "outside_shop_confirmed"):
            self.assertEqual(self.confirm(**{field: False}).status_code, 400)
            self.assertEqual(self.confirm(**{field: "true"}).status_code, 400)
        self.assertEqual(self.confirm(status="completed").status_code, 400)

    def test_stale_future_and_outside_staff_location_rejected(self):
        for seconds in (61, -30):
            self.assertEqual(self.confirm(location_captured_at=(timezone.now() - timedelta(seconds=seconds)).isoformat()).status_code, 400)
        self.assertEqual(self.confirm(latitude="19.000000").status_code, 400)

    def test_fresh_customer_outside_location_blocks_and_clears_after_completion(self):
        self.client.force_authenticate(self.customer)
        url = f"/api/orders/{self.order.pk}/handover-location/"
        payload = {key: val for key, val in self.payload(latitude="19.000000").items() if key in ("latitude", "longitude", "location_captured_at")}
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["within_radius"])
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.confirm().status_code, 400)
        self.client.force_authenticate(self.customer)
        payload["latitude"] = "18.520430"
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 200)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.confirm().status_code, 200)
        self.assertIsNone(CustomerHandoverLocation.objects.get().latitude)

    def test_legacy_otp_and_complete_routes_are_retired(self):
        for path in (f"/api/orders/{self.order.pk}/handover-otp/", f"/api/orders/{self.order.pk}/handover-otp/reissue/", f"/api/orders/{self.order.pk}/verify-pickup/", f"/api/shop-admin/orders/{self.order.pk}/complete/"):
            self.assertEqual(self.client.post(path, {"otp": "000000", "status": "completed"}, format="json").status_code, 410)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ready")


class StaffHandoverConcurrencyTests(StaffHandoverFixture, TransactionTestCase):
    def test_two_simultaneous_confirmations_complete_once(self):
        self.assertEqual(connection.vendor, "postgresql", "Concurrency acceptance requires PostgreSQL")
        barrier = Barrier(2)
        payload = self.payload()

        def worker():
            close_old_connections()
            try:
                user = User.objects.get(pk=self.owner.pk)
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait(timeout=10)
                response = client.post(self.url, payload, format="json")
                return response.status_code, response.data.get("already_completed")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(results), [(200, False), (200, True)])
        self.assertEqual(StaffHandoverConfirmation.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(order=self.order).count(), 1)


class CheckoutPreservationTests(StaffHandoverFixture, TestCase):
    def setUp(self):
        super().setUp()
        from datetime import date
        from customers.models import Cart, CartItem
        self.customer.date_of_birth = date(1990, 1, 1)
        self.customer.phone = "9876543210"
        self.customer.age_self_declared = True
        self.customer.age_self_declared_at = timezone.now()
        self.customer.save()
        self.shop.is_open = True
        self.shop.save()
        self.cart = Cart.objects.create(customer=self.profile)
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        self.client.force_authenticate(self.customer)

    def checkout_body(self, mode="walk_in"):
        body = {"pickup_mode": mode, "latitude": "18.520430", "longitude": "73.856744"}
        if mode == "vehicle":
            body["vehicle_id"] = self.vehicle.pk
        return body

    def test_vehicle_cannot_be_deleted_until_order_is_terminal(self):
        created = self.client.post("/api/orders/create/", self.checkout_body("vehicle"), format="json")
        self.assertEqual(created.status_code, 201)
        url = f"/api/customer/vehicles/{self.vehicle.pk}/"
        self.assertEqual(self.client.delete(url).status_code, 409)
        self.assertEqual(self.client.post(f"/api/orders/{created.data['id']}/cancel/").status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 204)

    def test_address_edit_preserves_user_age_declaration(self):
        self.assertIsNone(self.profile.date_of_birth)
        response = self.client.patch("/api/customer/profile/", {"address_line1": "New road"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.date_of_birth.isoformat(), "1990-01-01")
        self.assertTrue(self.customer.age_self_declared)

    def test_both_modes_keep_server_price_fee_and_single_stock_deduction(self):
        from customers.models import CartItem
        for mode in ("walk_in", "vehicle"):
            CartItem.objects.update_or_create(cart=self.cart, product=self.product, defaults={"quantity": 1})
            before = Product.objects.get(pk=self.product.pk).stock_quantity
            preview = self.client.post("/api/customer/checkout/preview/", self.checkout_body(mode), format="json")
            self.assertEqual(preview.status_code, 200, preview.data)
            self.assertEqual(preview.data["subtotal"], "500.00")
            self.assertEqual(preview.data["application_fee"], "50.00")
            self.assertEqual(preview.data["total_amount"], "550.00")
            self.assertEqual(Product.objects.get(pk=self.product.pk).stock_quantity, before)
            response = self.client.post("/api/orders/create/", self.checkout_body(mode), format="json")
            self.assertEqual(response.status_code, 201, response.data)
            created = Order.objects.get(pk=response.data["id"])
            self.assertEqual(created.pickup_mode, mode)
            self.assertEqual(created.vehicle_id, self.vehicle.pk if mode == "vehicle" else None)
            self.assertEqual(created.total_amount, Decimal("550.00"))
            self.assertEqual(Product.objects.get(pk=self.product.pk).stock_quantity, before - 1)

    def test_100m_restriction_preserves_stock(self):
        response = self.client.post("/api/orders/create/", {**self.checkout_body(), "latitude": "19.000000"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock_quantity, 8)

    def test_minimum_subtotal_preserved(self):
        self.product.price = Decimal("499.00")
        self.product.save()
        self.assertEqual(self.client.post("/api/orders/create/", self.checkout_body(), format="json").status_code, 400)

    def test_total_with_fee_routes_to_super_admin(self):
        self.product.price = Decimal("14950.00")
        self.product.save()
        response = self.client.post("/api/orders/create/", self.checkout_body(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending_super_admin")
