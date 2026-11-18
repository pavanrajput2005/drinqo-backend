"""Source-only regression coverage for payment operations; not executed here."""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from orders.models import OrderPayment, PaymentOperationalCase, PaymentRefund
from orders.payments import PaymentError, payment_projection, request_refund_record, try_refund_order, reconcile_operational_payment
from orders.razorpay_gateway import GatewayError
from orders.test_payments import PaymentFixture, TEST_GATEWAY


@override_settings(DRINQO_RAZORPAY=TEST_GATEWAY)
class PaymentOperationsTests(PaymentFixture, TestCase):
    def test_confirmed_order_can_start_payment_before_ledger_exists(self):
        self.assertTrue(payment_projection(self.order)["payment_eligible"])
        self.account.is_enabled = False
        self.account.save(update_fields=["is_enabled"])
        self.assertFalse(payment_projection(self.order)["payment_eligible"])
        self.assertFalse(OrderPayment.objects.filter(order=self.order).exists())
        self.gateway.create_order.assert_not_called()

    def test_reviewed_refund_recovers_by_lookup_only(self):
        self._initiate()
        self.assertEqual(self._verify().status_code, 200)
        self.assertEqual(self.client.post(reverse("order-cancel", args=[self.order.pk])).status_code, 200)
        self.gateway.refund.side_effect = GatewayError("timeout", "Refund outcome unknown.")
        try_refund_order(self.order.pk)
        self.assertEqual(self._payment().status, "review")
        self.gateway.fetch_refunds.return_value = []
        with self.assertRaises(PaymentError):
            reconcile_operational_payment(self._payment())
        self.assertEqual(self._payment().status, "review")
        self.gateway.fetch_refunds.return_value = [self._refund()]
        self.assertEqual(reconcile_operational_payment(self._payment()).status, "refunded")
        self.gateway.refund.assert_called_once()

    def test_projection_never_claims_unconfirmed_payment_is_paid(self):
        self._initiate()
        projection = payment_projection(self.order)
        self.assertEqual(projection["ledger_status"], "created")
        self.assertTrue(projection["payment_eligible"])
        self.assertEqual(self.order.payment_status, "pending")

    def test_refund_records_are_idempotent_per_payment_and_amount(self):
        self._initiate()
        payment = OrderPayment.objects.get(order=self.order)
        first = PaymentRefund.objects.create(payment=payment, amount=payment.amount)
        self.assertEqual(first.status, "requested")
        with self.assertRaises(Exception):
            PaymentRefund.objects.create(payment=payment, amount=payment.amount)

    def test_operational_case_is_not_customer_visible(self):
        self._initiate()
        payment = OrderPayment.objects.get(order=self.order)
        PaymentOperationalCase.objects.create(
            payment=payment, kind="settlement", reason_code="transfer_pending",
        )
        projection = payment_projection(self.order)
        self.assertNotIn("provider_account_id", projection)
        self.assertNotIn("provider_order_id", projection)

    def test_partial_refunds_cannot_exceed_captured_amount(self):
        self._initiate()
        payment = OrderPayment.objects.get(order=self.order)
        first = request_refund_record(payment, payment.amount - 1)
        self.assertEqual(first.amount, payment.amount - 1)
        with self.assertRaises(PaymentError):
            request_refund_record(payment, 2)

    def test_500_subtotal_and_50_fee_are_55000_paise(self):
        self.order.subtotal = Decimal("500.00")
        self.order.application_fee = Decimal("50.00")
        self.order.total_amount = Decimal("550.00")
        self.order.save(update_fields=["subtotal", "application_fee", "total_amount"])
        self._initiate()
        payment = OrderPayment.objects.get(order=self.order)
        self.assertEqual(payment.amount, 55000)
        self.assertEqual(payment.shop_amount, 50000)
        self.assertEqual(payment.platform_fee, 5000)

    def test_super_admin_operations_are_not_customer_or_shop_admin_accessible(self):
        self._initiate()
        payment = OrderPayment.objects.get(order=self.order)
        detail = reverse("super-admin-payment-detail", args=[payment.pk])
        cases = reverse("super-admin-payment-cases")
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(cases).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(detail).status_code, 200)
