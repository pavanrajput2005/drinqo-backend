"""PostgreSQL inventory regression SOURCE; execution requires separate approval.

KNOWN LOCK-ORDER RISK: test_checkout_and_high_value_rejection_preserve_inventory
runs the real joined checkout FOR UPDATE while rejection owns shop/order locks.
Its success expectation is deliberately NOT weakened to accept a deadlock or a
lock timeout. PostgreSQL query plans determine whether the present joined query
acquires product before shop; failure may expose the documented inversion.
No production locking or business logic is patched/replaced by this module.

All SQL below is test source only. Worker SQL deadlines bound database waits;
an approved launcher must additionally enforce its outer process deadline.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from threading import Barrier, Event
from time import monotonic
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import (
    Cart, CartItem, CustomerProfile, CustomerVehicle,
    CustomerVerificationSession, Notification,
)
from orders.models import Order, OrderItem, StaffHandoverConfirmation
from orders.testing_handover import staff_confirmation_payload
from orders.testing_handover import HandoverFixture
from products.models import Product, StockMovement
from shop_admin.models import SuperAdminAuditLog
from shop_admin import super_admin_views


class InventoryConcurrencyTests(HandoverFixture, TransactionTestCase):
    """Synthetic committed fixtures; never use SQLite as concurrency evidence."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires explicitly approved isolated PostgreSQL")
        super().setUp()
        self.shop.latitude = Decimal("18.520430")
        self.shop.longitude = Decimal("73.856744")
        self.shop.save(update_fields=["latitude", "longitude"])
        self.profile, self.vehicle, self.cart = self._checkout_identity(self.customer, "one")
        self.product = Product.objects.create(
            shop=self.shop, name="Synthetic reserved bottle", category="other",
            price=Decimal("7500.00"), stock_quantity=8,
        )
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        # Ten initial units minus two already reserved on this historical order.
        self.high_order = Order.objects.create(
            customer=self.customer, shop=self.shop, status="pending_super_admin",
            pickup_mode="vehicle", vehicle=self.vehicle, subtotal="15000.00",
            application_fee="50.00", total_amount="15050.00",
        )
        self._item(self.high_order, 2)
        self._set_timeouts()

    def _checkout_identity(self, customer, suffix):
        customer.date_of_birth = date(2000, 1, 1)
        customer.age_verified = True
        customer.age_verified_at = timezone.now()
        customer.phone = "9876543210"
        customer.save(update_fields=["date_of_birth", "age_verified", "age_verified_at", "phone"])
        proof = CustomerVerificationSession.objects.create(
            user=customer, document="private_customer_verification/synthetic-age.pdf",
            document_type="age_proof", status="approved", reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )
        SuperAdminAuditLog.objects.create(
            actor=self.admin, actor_email=self.admin.email,
            action="customer_document_approved", resource_type="verification_session",
            resource_id=proof.id, summary="Synthetic age proof approved.",
            metadata={"age_proof": True, "confirmed_date_of_birth": "2000-01-01", "age_verified": True},
        )
        profile = CustomerProfile.objects.create(
            user=customer, full_name="Synthetic vehicle customer " + suffix,
            address_line1="1 Synthetic Road", city="Pune", state="MH", postal_code="411001",
        )
        vehicle = CustomerVehicle.objects.create(
            customer=profile, vehicle_type="car", vehicle_number="TEST-" + suffix.upper(),
            vehicle_name="Synthetic vehicle",
        )
        return profile, vehicle, Cart.objects.create(customer=profile)

    def _item(self, order, quantity):
        return OrderItem.objects.create(
            order=order, product=self.product, product_name=self.product.name,
            unit_price=self.product.price, quantity=quantity,
            subtotal=self.product.price * quantity,
        )

    @staticmethod
    def _set_timeouts():
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
            cursor.execute("SET statement_timeout TO '30s'")
            cursor.execute("SET idle_in_transaction_session_timeout TO '60s'")

    def _checkout(self, customer=None, vehicle=None, pickup_mode="vehicle"):
        client = APIClient()
        client.force_authenticate(customer or self.customer)
        payload = {
            "pickup_mode": pickup_mode,
            "latitude": "18.520430", "longitude": "73.856744",
        }
        if pickup_mode == "vehicle":
            payload["vehicle_id"] = (vehicle or self.vehicle).id
        return client.post(reverse("order-create"), payload, format="json")

    def _reject(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        return client.post(
            reverse("super-admin-high-value-order-reject", args=[self.high_order.id]),
            {"reason": "Synthetic compliance rejection."}, format="json",
        )

    def _cancel(self, order):
        client = APIClient()
        client.force_authenticate(self.customer)
        return client.post(reverse("order-cancel", args=[order.id]), {}, format="json")

    def _worker(self, action, barrier=None, wrapper=None):
        close_old_connections()
        try:
            self._set_timeouts()
            if barrier is not None:
                barrier.wait(timeout=10)
            try:
                if wrapper is None:
                    response = action()
                else:
                    with connection.execute_wrapper(wrapper):
                        response = action()
                return {"status": response.status_code, "data": response.data}
            except DatabaseError as exc:
                # Do not convert contention failures into successful test outcomes.
                cause = exc.__cause__
                return {"database_error": type(exc).__name__, "sqlstate": getattr(cause, "sqlstate", None)}
        finally:
            connection.close()

    @contextmanager
    def _workers(self, count):
        executor = ThreadPoolExecutor(max_workers=count)
        try:
            yield executor
        finally:
            # A future timeout must not enter an unbounded executor join. Worker
            # sessions retain SQL deadlines and close in finally; the approved
            # launcher supplies the final wall-clock/process-tree kill boundary.
            executor.shutdown(wait=False, cancel_futures=True)

    def _race(self, *actions):
        barrier = Barrier(len(actions))
        with self._workers(len(actions)) as executor:
            futures = [executor.submit(self._worker, action, barrier) for action in actions]
            return [future.result(timeout=40) for future in futures]

    def _assert_not_completed(self):
        self.assertFalse(Order.objects.filter(pk=self.high_order.pk, status="completed").exists())
        self.assertFalse(Order.objects.filter(pk=self.high_order.pk, pickup_verified=True).exists())

    def test_checkout_and_manual_adjustment_preserve_both_stock_changes(self):
        def restock():
            client = APIClient()
            client.force_authenticate(self.owner)
            return client.post(reverse("shop_admin_adjust_inventory", args=[self.product.pk]),
                               {"quantity_change": 2}, format="json")
        results = self._race(self._checkout, restock)
        self.assertEqual([r.get("status") for r in results], [201, 201], results)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 9)

    def test_checkout_and_high_value_rejection_preserve_inventory(self):
        """KNOWN RISK: may FAIL with deadlock/timeout under current joined lock ordering."""
        rejection_locked = Event()
        release_rejection = Event()
        checkout_query_started = Event()
        pids = {}
        original_lock = super_admin_views.lock_order_with_shop

        def pause_after_real_shop_order_lock(order_id, **scope):
            order = original_lock(order_id, **scope)
            if order_id == self.high_order.id:
                pids["rejection"] = connection.connection.info.backend_pid
                rejection_locked.set()
                if not release_rejection.wait(timeout=8):
                    raise AssertionError("Observer did not release rejection before lock deadline")
            return order

        def observe_real_checkout_query(execute, statement, params, many, context):
            if '"customers_cartitem"' in statement and "FOR UPDATE" in statement.upper():
                pids["checkout"] = context["connection"].connection.info.backend_pid
                checkout_query_started.set()
            return execute(statement, params, many, context)

        with patch("shop_admin.super_admin_views.lock_order_with_shop", side_effect=pause_after_real_shop_order_lock):
            with self._workers(2) as executor:
                rejected = executor.submit(self._worker, self._reject)
                try:
                    self.assertTrue(rejection_locked.wait(timeout=10), "Rejection never acquired real locks")
                    checked_out = executor.submit(self._worker, self._checkout, None, observe_real_checkout_query)
                    self.assertTrue(checkout_query_started.wait(timeout=3), "Checkout never reached joined locking query")
                    deadline = monotonic() + 2
                    blocked_by_rejection = False
                    # Poll lock metadata, not business data; no sleep-based scheduling.
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_blocking_pids(%s)", [pids["checkout"]])
                            blockers = cursor.fetchone()[0]
                        if pids["rejection"] in blockers:
                            blocked_by_rejection = True
                            break
                    self.assertTrue(blocked_by_rejection, "The intended shop-lock contention was not established")
                finally:
                    release_rejection.set()
                outcomes = [checked_out.result(timeout=40), rejected.result(timeout=40)]
        self.assertEqual([result.get("status") for result in outcomes], [201, 200], outcomes)
        self.product.refresh_from_db()
        self.high_order.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 9)  # 8 - checkout(1) + restore(2).
        self.assertEqual(self.high_order.status, "cancelled")
        self.assertEqual(Order.objects.filter(customer=self.customer, status="pending").count(), 1)
        self.assertFalse(self.cart.items.exists())
        self._assert_not_completed()

    def test_concurrent_high_value_rejections_restore_once(self):
        outcomes = self._race(self._reject, self._reject)
        self.assertEqual(sorted(result.get("status", -1) for result in outcomes), [200, 400], outcomes)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertEqual(SuperAdminAuditLog.objects.filter(
            action="high_value_order_rejected", resource_type="order", resource_id=self.high_order.id,
        ).count(), 1)
        self.assertEqual(self._cancel(self.high_order).status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)
        self._assert_not_completed()

    def test_concurrent_customer_cancellations_restore_once(self):
        order = Order.objects.create(
            customer=self.customer, shop=self.shop, status="pending", subtotal="7500.00",
            application_fee="50.00", total_amount="7550.00", pickup_mode="vehicle", vehicle=self.vehicle,
        )
        self._item(order, 1)
        self.product.stock_quantity = 7
        self.product.save(update_fields=["stock_quantity", "updated_at"])
        outcomes = self._race(lambda: self._cancel(order), lambda: self._cancel(order))
        self.assertEqual(sorted(result.get("status", -1) for result in outcomes), [200, 409], outcomes)
        self.product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertEqual(order.status, "cancelled")
        self.assertFalse(order.pickup_verified)

    def test_competing_checkouts_cannot_make_stock_negative(self):
        other = User.objects.create_user(username="inventory-second", email="inventory-second@example.com", user_type="customer")
        _, vehicle, cart = self._checkout_identity(other, "two")
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        self.product.stock_quantity = 1
        self.product.save(update_fields=["stock_quantity", "updated_at"])
        outcomes = self._race(self._checkout, lambda: self._checkout(other, vehicle))
        self.assertEqual(sorted(result.get("status", -1) for result in outcomes), [201, 400], outcomes)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 0)
        self.assertEqual(Order.objects.filter(status="pending", shop=self.shop).count(), 1)
        self.assertEqual(CartItem.objects.filter(product=self.product).count(), 1)
        self._assert_not_completed()

    def test_walk_in_and_vehicle_compete_for_last_stock(self):
        other = User.objects.create_user(username="mixed-pickup", user_type="customer")
        _, vehicle, cart = self._checkout_identity(other, "mixed")
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        self.product.stock_quantity = 1
        self.product.save(update_fields=["stock_quantity", "updated_at"])
        outcomes = self._race(
            lambda: self._checkout(pickup_mode="walk_in"),
            lambda: self._checkout(other, vehicle),
        )
        self.assertEqual(sorted(result.get("status", -1) for result in outcomes), [201, 400], outcomes)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 0)
        self.assertEqual(Order.objects.filter(status="pending", shop=self.shop).count(), 1)
        self.assertEqual(CartItem.objects.filter(product=self.product).count(), 1)

    def test_walk_in_double_click_reserves_once(self):
        outcomes = self._race(
            lambda: self._checkout(pickup_mode="walk_in"),
            lambda: self._checkout(pickup_mode="walk_in"),
        )
        self.assertEqual(sorted(result.get("status", -1) for result in outcomes), [201, 400], outcomes)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)
        self.assertEqual(Order.objects.filter(status="pending", pickup_mode="walk_in", shop=self.shop).count(), 1)
        self.assertFalse(self.cart.items.exists())

    def test_walk_in_stock_failure_rolls_back_order_and_cart(self):
        before = Order.objects.count()
        self._fail_after_stock_save(lambda: self._checkout(pickup_mode="walk_in"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertEqual(Order.objects.count(), before)
        self.assertEqual(self.cart.items.get().quantity, 1)
        self.assertFalse(Notification.objects.filter(notification_type="order_placed").exists())

    def test_staff_handover_concurrent_with_checkout_does_not_deduct_stock(self):
        self.order.vehicle = self.vehicle
        self.order.subtotal = "7500.00"
        self.order.total_amount = "7550.00"
        self.order.save()
        from orders.testing_payments import record_captured_payment_for_test
        record_captured_payment_for_test(self.order)
        self._item(self.order, 1)
        self.product.stock_quantity = 7
        self.product.save(update_fields=["stock_quantity", "updated_at"])
        payload = staff_confirmation_payload(self.customer)
        url = reverse("staff-confirm-handover", args=[self.order.id])

        def confirm():
            client = APIClient()
            client.force_authenticate(self.owner)
            return client.post(url, payload, format="json")

        outcomes = self._race(confirm, self._checkout)
        self.assertEqual([result.get("status") for result in outcomes], [200, 201], outcomes)
        first = outcomes[0]["data"]
        self.assertIs(first["already_completed"], False)
        replay = confirm()
        self.assertEqual(replay.status_code, 200)
        self.assertIs(replay.data["already_completed"], True)
        record = StaffHandoverConfirmation.objects.get(order=self.order)
        self.assertEqual(first["confirmation_id"], record.pk)
        self.assertEqual(replay.data["confirmation_id"], record.pk)
        self.assertEqual(record.actor_id, self.owner.pk)
        self.product.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 6)  # Checkout alone reserves one.
        self.assertEqual(self.order.status, "completed")
        self.assertEqual(Notification.objects.filter(order=self.order, title="Order Completed").count(), 1)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())
        self._assert_not_completed()

    def _fail_after_stock_save(self, action):
        original_save = Product.save
        def fail_after_write(product, *args, **kwargs):
            result = original_save(product, *args, **kwargs)
            if product.pk == self.product.pk and "stock_quantity" in (kwargs.get("update_fields") or []):
                raise RuntimeError("Synthetic stock failure after database update")
            return result
        with patch.object(Product, "save", new=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, "Synthetic stock failure"):
                action()

    def test_checkout_stock_failure_rolls_back_reservation_and_order(self):
        before = Order.objects.count()
        self._fail_after_stock_save(self._checkout)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertEqual(Order.objects.count(), before)
        self.assertEqual(self.cart.items.get().quantity, 1)
        self.assertFalse(Notification.objects.filter(notification_type="order_placed").exists())
        self._assert_not_completed()

    def test_high_value_rejection_stock_failure_rolls_back_restoration(self):
        self._fail_after_stock_save(self._reject)
        self.product.refresh_from_db()
        self.high_order.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertEqual(self.high_order.status, "pending_super_admin")
        self.assertFalse(SuperAdminAuditLog.objects.filter(action="high_value_order_rejected").exists())
        self.assertFalse(Notification.objects.filter(order=self.high_order).exists())
        self._assert_not_completed()

    def test_cancellation_stock_failure_rolls_back_restoration(self):
        order = Order.objects.create(customer=self.customer, shop=self.shop, status="pending", subtotal="7500.00", total_amount="7550.00")
        self._item(order, 1)
        self.product.stock_quantity = 7
        self.product.save(update_fields=["stock_quantity", "updated_at"])
        self._fail_after_stock_save(lambda: self._cancel(order))
        self.product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)
        self.assertEqual(order.status, "pending")
        self.assertFalse(Notification.objects.filter(order=order).exists())

    def test_checkout_wait_for_shop_lock_is_bounded(self):
        initial_orders = Order.objects.count()
        started = Event()
        def observe(execute, statement, params, many, context):
            if '"customers_cartitem"' in statement and "FOR UPDATE" in statement.upper():
                started.set()
            return execute(statement, params, many, context)
        with self._workers(1) as executor:
            with transaction.atomic():
                self.shop.__class__.objects.select_for_update(of=("self",)).get(pk=self.shop.id)
                begin = monotonic()
                future = executor.submit(self._worker, self._checkout, None, observe)
                self.assertTrue(started.wait(timeout=3))
                outcome = future.result(timeout=10)
                elapsed = monotonic() - begin
        self.assertEqual(outcome.get("sqlstate"), "55P03", outcome)
        self.assertLess(elapsed, 10)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertEqual(Order.objects.count(), initial_orders)
        self.assertEqual(self.cart.items.get().quantity, 1)
