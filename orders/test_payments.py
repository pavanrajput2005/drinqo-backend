"""Payment regression source. Run only in an explicitly approved isolated DB.

Provider HTTP is replaced at the gateway boundary. Signatures use separate,
synthetic test secrets; no test requires a merchant account or live network.
"""
import hashlib
import hmac
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import CustomerProfile, CustomerVehicle, CustomerVerificationSession, Notification
from orders.models import Order, OrderHandoverOTP, OrderItem, OrderPayment, PaymentWebhookEvent, ShopPaymentAccount
from orders.payments import handle_webhook, initiate_payment, process_refund, verified_payment_exists, verify_customer_payment
from orders.razorpay_gateway import GatewayError, RazorpayGateway
from orders.testing_handover import HANDOVER_TEST_LOCATION, staff_confirmation_payload
from orders.testing_payments import record_captured_payment_for_test
from products.models import Product, StockMovement
from shop_admin.models import SuperAdminAuditLog
from shops.models import Shop, ShopLicenseDocument


TEST_GATEWAY = {
    "enabled": True, "key_id": "rzp_test_SyntheticOnly",
    "key_secret": "synthetic-checkout-secret-only-0123456789",
    "webhook_secrets": ["synthetic-webhook-secret-independent-0123456789"],
}


class PaymentFixture:
    def setUp(self):
        super().setUp()
        self.handover_location = dict(HANDOVER_TEST_LOCATION)
        cache = override_settings(CACHES={"default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": uuid.uuid4().hex,
        }})
        cache.enable()
        self.addCleanup(cache.disable)
        self.customer = User.objects.create_user(
            username="payment-customer", email="payment-customer@example.com", user_type="customer",
            date_of_birth=date(2000, 1, 1), age_verified=True, age_verified_at=timezone.now(),
            phone="9876543210",
        )
        profile = CustomerProfile.objects.create(
            user=self.customer, full_name="Synthetic payment customer",
            address_line1="1 Synthetic Road", city="Pune", state="Maharashtra", postal_code="411001",
        )
        self.other_customer = User.objects.create_user(
            username="payment-other", email="payment-other@example.com", user_type="customer",
        )
        self.owner = User.objects.create_user(username="payment-owner", email="payment-owner@example.com", user_type="shop_admin")
        self.admin = User.objects.create_user(username="payment-admin", email="payment-admin@example.com", user_type="super_admin")
        proof = CustomerVerificationSession.objects.create(
            user=self.customer, document="private_customer_verification/synthetic-payment-age.pdf",
            document_type="age_proof", status="approved", reviewed_by=self.admin, reviewed_at=timezone.now(),
        )
        SuperAdminAuditLog.objects.create(
            actor=self.admin, actor_email=self.admin.email, action="customer_document_approved",
            resource_type="verification_session", resource_id=proof.id, summary="Synthetic test age proof.",
            metadata={"age_proof": True, "confirmed_date_of_birth": "2000-01-01", "age_verified": True},
        )
        self.shop = Shop.objects.create(
            owner=self.owner, shop_name="Synthetic Payment Shop", license_number="PAYMENT-TEST",
            license_status="verified", is_active=True, **HANDOVER_TEST_LOCATION,
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop, license_number="PAYMENT-TEST", document="private_shop_licenses/synthetic-payment.pdf",
            verification_status="verified", expiry_date=timezone.localdate() + timedelta(days=365),
        )
        self.account = ShopPaymentAccount.objects.create(
            shop=self.shop, provider_account_id="acc_SyntheticShop", is_enabled=True,
        )
        self.order = Order.objects.create(
            customer=self.customer, shop=self.shop, status="confirmed", pickup_mode="vehicle",
            vehicle=CustomerVehicle.objects.create(customer=profile, vehicle_type="car", vehicle_number="TEST-01"),
            subtotal=Decimal("1000.00"), application_fee=Decimal("50.00"), total_amount=Decimal("1050.00"),
        )
        self.product = Product.objects.create(
            shop=self.shop, name="Already reserved synthetic bottle", category="other", price="1000.00", stock_quantity=9,
        )
        OrderItem.objects.create(
            order=self.order, product=self.product, product_name=self.product.name,
            unit_price="1000.00", quantity=1, subtotal="1000.00",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.customer)
        gateway_patch = patch("orders.payments.RazorpayGateway", autospec=True)
        self.gateway_factory = gateway_patch.start()
        self.addCleanup(gateway_patch.stop)
        self.gateway = self.gateway_factory.return_value
        self.gateway.key_id = TEST_GATEWAY["key_id"]
        self.gateway.create_order.side_effect = self._create_order
        self.gateway.fetch_order.side_effect = lambda _: self._remote_order(paid=True)
        self.gateway.fetch_payment.side_effect = lambda _: self._capture()
        self.gateway.refund.side_effect = lambda *args: self._refund(status="processed")

    def _payment(self):
        return OrderPayment.objects.get(order=self.order)

    def _remote_order(self, *, paid=False):
        payment = self._payment()
        return {
            "entity": "order", "id": payment.provider_order_id or "order_SyntheticOne",
            "receipt": payment.pk.hex, "amount": payment.amount, "currency": "INR",
            "partial_payment": False, "status": "paid" if paid else "created",
            "amount_paid": payment.amount if paid else 0,
            "amount_due": 0 if paid else payment.amount,
            "transfers": [{
                "id": "trf_SyntheticOne", "entity": "transfer", "source": payment.provider_order_id or "order_SyntheticOne",
                "recipient": payment.provider_account_id, "amount": payment.shop_amount,
                "currency": "INR", "on_hold": False,
            }],
        }

    def _create_order(self, receipt, amount, shop_amount, account_id):
        payment = self._payment()
        self.assertEqual((receipt, amount, shop_amount, account_id), (
            payment.pk.hex, payment.amount, payment.shop_amount, payment.provider_account_id,
        ))
        return self._remote_order()

    def _capture(self, **changes):
        payment = self._payment()
        return {
            "entity": "payment", "id": "pay_SyntheticOne", "order_id": payment.provider_order_id,
            "amount": payment.amount, "currency": "INR", "status": "captured", "captured": True,
            "amount_refunded": 0, **changes,
        }

    def _refund(self, *, status="processed", **changes):
        payment = self._payment()
        return {
            "entity": "refund", "id": "rfnd_SyntheticOne", "payment_id": payment.provider_payment_id,
            "amount": payment.amount, "currency": "INR", "status": status,
            "receipt": payment.pk.hex, **changes,
        }

    def _initiate(self):
        response = self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def _signature(self, *, order_id=None, payment_id="pay_SyntheticOne"):
        provider_order_id = order_id or self._payment().provider_order_id
        return hmac.new(
            TEST_GATEWAY["key_secret"].encode("ascii"), f"{provider_order_id}|{payment_id}".encode("ascii"), hashlib.sha256,
        ).hexdigest()

    def _verify(self, **changes):
        return self.client.post(reverse("payment-verify", args=[self.order.id]), {
            "razorpay_order_id": self._payment().provider_order_id,
            "razorpay_payment_id": "pay_SyntheticOne", "razorpay_signature": self._signature(), **changes,
        }, format="json")

    def _webhook_bytes(self, event_type="payment.captured", entity=None):
        key = "refund" if event_type.startswith("refund.") else "payment"
        payload = self._capture() if entity is None else entity
        body = json.dumps({"event": event_type, "payload": {key: {"entity": payload}}}, separators=(",", ":")).encode()
        signature = hmac.new(TEST_GATEWAY["webhook_secrets"][0].encode(), body, hashlib.sha256).hexdigest()
        return body, signature

    def _webhook(self, *, event_id="evt_SyntheticOne", event_type="payment.captured", entity=None):
        body, signature = self._webhook_bytes(event_type, entity)
        client = APIClient()  # Provider authentication deliberately does not use customer JWT.
        return client.post(reverse("razorpay-webhook"), body, content_type="application/json",
                           HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id)

    def _assert_not_captured(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "pending")
        self.assertIsNone(self._payment().captured_at)
        self.assertFalse(Notification.objects.filter(order=self.order, notification_type="payment").exists())


@override_settings(DRINQO_RAZORPAY=TEST_GATEWAY)
class PaymentApiTests(PaymentFixture, TestCase):
    def test_amount_and_route_split_come_only_from_order(self):
        response = self._initiate()
        self.assertEqual(response.data["checkout"]["amount"], 105000)
        self.assertEqual(response.data["checkout"]["currency"], "INR")
        self.assertEqual(response.data["checkout"]["key"], TEST_GATEWAY["key_id"])
        self.assertEqual(self._payment().shop_amount, 100000)
        self.assertEqual(self._payment().platform_fee, 5000)
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertNotIn("key_secret", response.data)
        self.assertNotIn("provider_account_id", response.data)

    def test_repeated_initialization_reuses_one_provider_order(self):
        first = self._initiate()
        second = self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["checkout"], second.data["checkout"])
        self.gateway.create_order.assert_called_once()
        self.assertEqual(OrderPayment.objects.filter(order=self.order).count(), 1)

    def test_client_cannot_override_amount_or_destination(self):
        for payload in ({"amount": 1}, {"platform_fee": 0}, {"account": "acc_Attacker"}, {"payment_status": "paid"}):
            response = self.client.post(reverse("payment-initiate", args=[self.order.id]), payload, format="json")
            self.assertEqual(response.status_code, 400)
        self.assertFalse(OrderPayment.objects.exists())
        self.gateway.create_order.assert_not_called()

    def test_confirmation_is_required_before_initialization(self):
        self.order.status = "pending"
        self.order.save(update_fields=["status"])
        response = self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(OrderPayment.objects.exists())

    def test_high_value_payment_still_requires_super_admin_approval(self):
        self.order.subtotal = Decimal("14950.00")
        self.order.total_amount = Decimal("15000.00")
        self.order.save(update_fields=["subtotal", "total_amount"])
        blocked = self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json")
        self.assertEqual(blocked.status_code, 403)
        self.assertFalse(OrderPayment.objects.exists())
        self.order.super_admin_approved_at = timezone.now()
        self.order.super_admin_approved_by = self.admin
        self.order.save(update_fields=["super_admin_approved_at", "super_admin_approved_by"])
        self.assertEqual(self._initiate().data["amount"], 1500000)

    def test_age_proof_and_shop_onboarding_are_required(self):
        self.customer.age_verified = False
        self.customer.save(update_fields=["age_verified"])
        self.assertEqual(self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json").status_code, 403)
        self.customer.age_verified = True
        self.customer.save(update_fields=["age_verified"])
        self.account.is_enabled = False
        self.account.save(update_fields=["is_enabled"])
        self.assertEqual(self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json").status_code, 503)
        self.assertFalse(OrderPayment.objects.exists())

    def test_customer_ownership_roles_and_authentication(self):
        self.client.force_authenticate(self.other_customer)
        for name in ("payment-initiate", "payment-reconcile"):
            self.assertEqual(self.client.post(reverse(name, args=[self.order.id]), {}, format="json").status_code, 404)
        for actor in (self.owner, self.admin):
            self.client.force_authenticate(actor)
            self.assertEqual(self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json").status_code, 403)
        client = APIClient()
        self.assertEqual(client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json").status_code, 401)

    def test_payment_verification_is_customer_owned_and_super_admin_cannot_override(self):
        self._initiate()
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self._verify().status_code, 404)
        for actor in (self.owner, self.admin):
            self.client.force_authenticate(actor)
            self.assertEqual(self._verify().status_code, 403)
        self.gateway.fetch_payment.assert_not_called()
        self._assert_not_captured()

    def test_missing_configuration_and_live_keys_fail_closed(self):
        for config in ({}, {**TEST_GATEWAY, "key_id": "rzp_live_NotAllowed"}):
            with self.subTest(configured=bool(config)), override_settings(DRINQO_RAZORPAY=config):
                with patch("orders.payments.RazorpayGateway", RazorpayGateway):
                    response = self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json")
                self.assertEqual(response.status_code, 503)
        self.assertFalse(OrderPayment.objects.exists())

    def test_api_key_identity_rotation_requires_review_of_existing_payment(self):
        self._initiate()
        rotated_key_id = "rzp_test_ReplacementMerchantKey"
        self.gateway.key_id = rotated_key_id
        with override_settings(DRINQO_RAZORPAY={**TEST_GATEWAY, "key_id": rotated_key_id}):
            self.assertEqual(self._verify().status_code, 409)
        self.gateway.fetch_payment.assert_not_called()
        self._assert_not_captured()

    def test_forged_signature_does_not_fetch_or_mark_paid(self):
        self._initiate()
        response = self._verify(razorpay_signature="0" * 64)
        self.assertEqual(response.status_code, 400)
        self.gateway.fetch_payment.assert_not_called()
        self._assert_not_captured()

    def test_valid_signature_for_different_provider_order_is_rejected(self):
        self._initiate()
        response = self._verify(razorpay_order_id="order_Attacker", razorpay_signature=self._signature(order_id="order_Attacker"))
        self.assertEqual(response.status_code, 400)
        self.gateway.fetch_payment.assert_not_called()
        self._assert_not_captured()

    def test_authorized_underpaid_wrong_currency_or_order_cannot_be_captured(self):
        self._initiate()
        invalid = (
            {"status": "authorized", "captured": False}, {"amount": 1},
            {"currency": "USD"}, {"order_id": "order_OtherOrder"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                self.gateway.fetch_payment.side_effect = lambda _, values=changes: self._capture(**values)
                self.assertEqual(self._verify().status_code, 409)
                self._assert_not_captured()

    def test_unpaid_remote_order_blocks_capture_even_with_valid_payment_signature(self):
        self._initiate()
        self.gateway.fetch_order.side_effect = lambda _: self._remote_order(paid=False)
        self.assertEqual(self._verify().status_code, 409)
        self._assert_not_captured()

    def test_successful_callback_is_idempotent_and_does_not_touch_inventory(self):
        self._initiate()
        for _ in range(2):
            self.assertEqual(self._verify().status_code, 200)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertTrue(verified_payment_exists(self.order))
        self.assertEqual(self.order.status, "confirmed")
        self.assertEqual(self.product.stock_quantity, 9)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())
        self.assertEqual(Notification.objects.filter(order=self.order, title="Payment Received").count(), 1)

    def test_verified_capture_allows_preparation_then_staff_vehicle_handover(self):
        self._initiate()
        self.assertEqual(self._verify().status_code, 200)
        self.client.force_authenticate(self.owner)
        for state in ("preparing", "ready"):
            response = self.client.post(reverse("shop_admin_update_order_status", args=[self.order.id]),
                                        {"status": state}, format="json")
            self.assertEqual(response.status_code, 200)
        completed = self.client.post(reverse("staff-confirm-handover", args=[self.order.id]),
                                     staff_confirmation_payload(self.customer), format="json")
        self.assertEqual(completed.status_code, 200)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, "completed")
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self.product.stock_quantity, 9)
        self.assertEqual(Notification.objects.filter(order=self.order, title="Order Completed").count(), 1)

    def test_duplicate_webhook_and_callback_create_one_payment_notification(self):
        self._initiate()
        self.assertEqual(self._webhook().status_code, 200)
        self.assertEqual(self._webhook().status_code, 200)
        self.assertEqual(self._webhook(event_id="evt_SecondDelivery").status_code, 200)
        self.assertEqual(self._verify().status_code, 200)
        self.assertEqual(PaymentWebhookEvent.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(order=self.order, title="Payment Received").count(), 1)

    def test_signed_failed_payment_webhook_is_idempotent_and_never_marks_paid(self):
        self._initiate()
        failed = self._capture(status="failed", captured=False)
        self.assertEqual(
            self._webhook(event_id="evt_FailedPayment", event_type="payment.failed", entity=failed).status_code,
            200,
        )
        self.assertEqual(
            self._webhook(event_id="evt_FailedPayment", event_type="payment.failed", entity=failed).status_code,
            200,
        )
        payment = self._payment()
        self.order.refresh_from_db()
        self.assertEqual(payment.status, "created")
        self.assertEqual(payment.last_error, "payment_failed")
        self.assertEqual(self.order.payment_status, "pending")
        self.assertFalse(Notification.objects.filter(order=self.order, notification_type="payment").exists())
        self.assertEqual(PaymentWebhookEvent.objects.filter(event_id="evt_FailedPayment").count(), 1)

    def test_modified_webhook_body_and_conflicting_event_identity_are_rejected(self):
        self._initiate()
        body, signature = self._webhook_bytes()
        client = APIClient()
        response = client.post(reverse("razorpay-webhook"), body + b" ", content_type="application/json",
                               HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt_Tampered")
        self.assertEqual(response.status_code, 400)
        self._assert_not_captured()
        self.assertEqual(self._webhook().status_code, 200)
        self.assertEqual(self._webhook(entity=self._capture(amount=1)).status_code, 400)
        self.assertEqual(PaymentWebhookEvent.objects.count(), 1)

    def test_webhook_old_secret_retained_for_retry_then_retirement_rejects_it(self):
        self._initiate()
        new_secret = "synthetic-replacement-webhook-secret-0123456789"
        with override_settings(DRINQO_RAZORPAY={**TEST_GATEWAY, "webhook_secrets": [new_secret, *TEST_GATEWAY["webhook_secrets"]]}):
            self.assertEqual(self._webhook().status_code, 200)
        with override_settings(DRINQO_RAZORPAY={**TEST_GATEWAY, "webhook_secrets": [new_secret]}):
            self.assertEqual(self._webhook(event_id="evt_RetiredSecret").status_code, 400)

    def test_unknown_provider_order_is_retryable_without_persisting_payload(self):
        self._initiate()
        response = self._webhook(entity=self._capture(order_id="order_Unmapped"))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(PaymentWebhookEvent.objects.exists())
        self._assert_not_captured()

    def test_notification_failure_rolls_back_capture_and_paid_projection(self):
        self._initiate()
        with patch("orders.payments.Notification.objects.create", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                verify_customer_payment(self.customer, self.order.id, self._payment().provider_order_id,
                                        "pay_SyntheticOne", self._signature())
        self._assert_not_captured()
        self.assertEqual(self._payment().status, "created")

    def test_deduplication_insert_failure_rolls_back_capture_and_notification(self):
        self._initiate()
        body, signature = self._webhook_bytes()
        with patch("orders.payments.PaymentWebhookEvent.objects.create", side_effect=RuntimeError("synthetic event failure")):
            with self.assertRaises(RuntimeError):
                handle_webhook(body, signature, "evt_Rollback")
        self._assert_not_captured()
        self.assertFalse(PaymentWebhookEvent.objects.exists())

    def test_ambiguous_initialization_never_sends_second_create(self):
        self.gateway.create_order.side_effect = GatewayError("gateway_request_failed", "Synthetic provider timeout.")
        first = self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json")
        self.assertEqual(first.status_code, 503)
        self.assertEqual(self._payment().status, "review")
        self.gateway.find_order.return_value = None
        second = self.client.post(reverse("payment-initiate", args=[self.order.id]), {}, format="json")
        self.assertEqual(second.status_code, 409)
        self.gateway.create_order.assert_called_once()
        self.gateway.find_order.assert_called_once_with(self._payment().pk.hex)

    def test_late_initialization_finalizer_preserves_financial_review_state(self):
        def create_with_concurrent_review(*args):
            entity = self._create_order(*args)
            # Model a refund/reconciliation write committed while provider IO
            # was in flight; initialization must not overwrite its state.
            OrderPayment.objects.filter(order=self.order).update(
                status="review", last_error="refund_requires_review",
            )
            return entity
        self.gateway.create_order.side_effect = create_with_concurrent_review
        response = self._initiate()
        self.assertNotIn("checkout", response.data)
        self.assertEqual(self._payment().status, "review")
        self.assertEqual(self._payment().last_error, "refund_requires_review")
        self.assertEqual(self._verify().status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self._payment().status, "review")
        self.assertFalse(verified_payment_exists(self.order))

    def test_callback_lost_reconciliation_marks_only_authoritative_capture(self):
        self._initiate()
        self.gateway.fetch_order_payments.return_value = [self._capture()]
        response = self.client.post(reverse("payment-reconcile", args=[self.order.id]), {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(verified_payment_exists(self.order))
        self.assertEqual(Notification.objects.filter(order=self.order, title="Payment Received").count(), 1)

    def test_multiple_remote_captures_require_reconciliation(self):
        self._initiate()
        self.gateway.fetch_order_payments.return_value = [self._capture(), self._capture(id="pay_UnexpectedSecond")]
        response = self.client.post(reverse("payment-reconcile", args=[self.order.id]), {}, format="json")
        self.assertEqual(response.status_code, 409)
        self._assert_not_captured()

    def test_late_capture_after_cancellation_creates_refund_liability_without_revival(self):
        self._initiate()
        cancelled = self.client.post(reverse("order-cancel", args=[self.order.id]), {}, format="json")
        self.assertEqual(cancelled.status_code, 200)
        with patch("orders.payments.try_refund_order") as retry:
            self.assertEqual(self._webhook().status_code, 200)
        retry.assert_called_once_with(self.order.id)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self._payment().status, "refund_required")
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertFalse(verified_payment_exists(self.order))

    def test_refund_processed_is_single_notification_and_replay_cannot_restore_paid(self):
        self._initiate()
        self.assertEqual(self._verify().status_code, 200)
        with patch("orders.payments.try_refund_order"):
            self.assertEqual(self.client.post(reverse("order-cancel", args=[self.order.id]), {}, format="json").status_code, 200)
        process_refund(self.order.id)
        self.assertEqual(self._webhook(event_id="evt_Refund", event_type="refund.processed", entity=self._refund()).status_code, 200)
        self.assertEqual(self._webhook(event_id="evt_OldCapture").status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "refunded")
        self.assertEqual(self._payment().status, "refunded")
        self.assertEqual(Notification.objects.filter(order=self.order, title="Refund Processed").count(), 1)

    def test_ambiguous_refund_is_reconciled_without_second_refund_post(self):
        self._initiate()
        self.assertEqual(self._verify().status_code, 200)
        with patch("orders.payments.try_refund_order"):
            self.client.post(reverse("order-cancel", args=[self.order.id]), {}, format="json")
        self.gateway.refund.side_effect = GatewayError("gateway_request_failed", "Synthetic refund timeout.")
        with self.assertRaises(GatewayError):
            process_refund(self.order.id)
        self.assertEqual(self._payment().status, "refunding")
        self.gateway.fetch_refunds.return_value = []
        with self.assertRaises(GatewayError):
            process_refund(self.order.id)
        self.gateway.refund.assert_called_once()

    def test_partial_refund_then_stale_capture_cannot_reenable_paid_gate(self):
        self._initiate()
        self.assertEqual(self._verify().status_code, 200)
        partial = self._refund(amount=1000)
        self.assertEqual(self._webhook(event_id="evt_Partial", event_type="refund.processed", entity=partial).status_code, 200)
        self.assertEqual(self._webhook(event_id="evt_StaleCapture").status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self._payment().status, "review")
        self.assertGreaterEqual(self._payment().refunded_amount, 1000)
        self.assertFalse(verified_payment_exists(self.order))

    def test_full_refund_before_capture_webhook_never_enables_handover(self):
        self._initiate()
        refund = self._refund(payment_id="pay_SyntheticOne")
        self.gateway.fetch_payment.side_effect = lambda _: self._capture(
            status="refunded", amount_refunded=self._payment().amount,
        )
        response = self._webhook(event_id="evt_RefundArrivedFirst", event_type="refund.processed", entity=refund)
        self.assertEqual(response.status_code, 200)
        self.gateway.fetch_payment.assert_called_once_with("pay_SyntheticOne")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "refunded")
        self.assertEqual(self._payment().status, "refunded")
        self.assertFalse(verified_payment_exists(self.order))
        self.assertEqual(self._webhook(event_id="evt_DelayedCapture").status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "refunded")
        self.assertEqual(Notification.objects.filter(order=self.order, title="Refund Processed").count(), 1)

    def test_order_amount_change_after_initialization_requires_reconciliation(self):
        self._initiate()
        self.order.subtotal = Decimal("1100.00")
        self.order.total_amount = Decimal("1150.00")
        self.order.save(update_fields=["subtotal", "total_amount"])
        self.assertEqual(self._verify().status_code, 409)
        self.gateway.fetch_payment.assert_not_called()
        self._assert_not_captured()

    def test_unpaid_and_historical_paid_flag_cannot_prepare_or_complete(self):
        for payment_status in ("pending", "paid"):
            with self.subTest(payment_status=payment_status):
                self.order.payment_status = payment_status
                self.order.status = "confirmed"
                self.order.save(update_fields=["payment_status", "status"])
                self.client.force_authenticate(self.owner)
                preparing = self.client.post(reverse("shop_admin_update_order_status", args=[self.order.id]), {"status": "preparing"}, format="json")
                self.assertEqual(preparing.status_code, 402)
                self.order.status = "ready"
                self.order.save(update_fields=["status"])
                result = self.client.post(reverse("staff-confirm-handover", args=[self.order.id]),
                                          staff_confirmation_payload(self.customer), format="json")
                self.assertEqual(result.status_code, 402)
                self.assertFalse(OrderHandoverOTP.objects.filter(order=self.order).exists())

    def test_lost_paid_state_blocks_staff_completion(self):
        record_captured_payment_for_test(self.order)
        self.order.status = "ready"
        self.order.save(update_fields=["status"])
        OrderPayment.objects.filter(order=self.order).update(status="review")
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("staff-confirm-handover", args=[self.order.id]),
                                    staff_confirmation_payload(self.customer), format="json")
        self.assertEqual(response.status_code, 402)
        self.assertFalse(OrderHandoverOTP.objects.filter(order=self.order).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ready")

    def test_platform_fee_alias_is_same_charge_in_order_and_payment_status(self):
        for name in ("order-detail", "payment-status"):
            response = self.client.get(reverse(name, args=[self.order.id]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(Decimal(str(response.data["platform_fee"])), Decimal("50.00"))
            self.assertEqual(Decimal(str(response.data["application_fee"])), Decimal("50.00"))
            self.assertEqual(Decimal(str(response.data["total_amount"])), Decimal("1050.00"))


@override_settings(DRINQO_RAZORPAY=TEST_GATEWAY)
class PaymentConcurrencyTests(PaymentFixture, TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires explicitly approved isolated PostgreSQL")
        super().setUp()

    def _race(self, *actions):
        barrier = Barrier(len(actions))

        def submit(action):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout TO '5s'")
                    cursor.execute("SET statement_timeout TO '30s'")
                    cursor.execute("SET idle_in_transaction_session_timeout TO '60s'")
                barrier.wait(timeout=10)
                return action()
            finally:
                connection.close()

        executor = ThreadPoolExecutor(max_workers=len(actions))
        try:
            futures = [executor.submit(submit, action) for action in actions]
            return [future.result(timeout=45) for future in futures]
        finally:
            # An approved outer runner deadline remains necessary for blocked
            # Python threads; future cancellation cannot terminate running work.
            executor.shutdown(wait=False, cancel_futures=True)

    def test_capture_callback_and_webhook_commit_one_notification(self):
        self._initiate()
        body, webhook_signature = self._webhook_bytes()
        provider_order_id = self._payment().provider_order_id
        checkout_signature = self._signature()
        results = self._race(
            lambda: verify_customer_payment(self.customer, self.order.id, provider_order_id,
                                            "pay_SyntheticOne", checkout_signature)[1],
            lambda: handle_webhook(body, webhook_signature, "evt_ConcurrentCapture")[1],
        )
        self.assertEqual(results, [200, 200])
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertTrue(verified_payment_exists(self.order))
        self.assertEqual(self.product.stock_quantity, 9)
        self.assertEqual(PaymentWebhookEvent.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(order=self.order, title="Payment Received").count(), 1)

    def test_cancellation_and_capture_restore_stock_once_and_preserve_refund_liability(self):
        self._initiate()
        body, signature = self._webhook_bytes()

        def cancel():
            client = APIClient()
            client.force_authenticate(self.customer)
            return client.post(reverse("order-cancel", args=[self.order.id]), {}, format="json").status_code

        with patch("orders.payments.try_refund_order") as reconcile_refund:
            results = self._race(cancel, lambda: handle_webhook(body, signature, "evt_CancelCaptureRace")[1])
        self.assertEqual(results, [200, 200])
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self._payment().status, "refund_required")
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertFalse(verified_payment_exists(self.order))
        self.assertGreaterEqual(reconcile_refund.call_count, 1)
        self.assertEqual(Notification.objects.filter(order=self.order, title="Payment Received").count(), 1)
        self.assertEqual(Notification.objects.filter(order=self.order, title="Order Cancelled").count(), 1)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post(reverse("order-cancel", args=[self.order.id]), {}, format="json").status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)
