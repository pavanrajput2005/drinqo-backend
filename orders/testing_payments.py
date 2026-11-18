"""Explicit synthetic payment-ledger fixtures. Never import from application code.

These helpers do not call a provider, generate notifications, or verify real money.
Use them only when a test deliberately assumes an earlier captured payment. Call
again explicitly after changing an order's amounts to refresh that assumption.
"""
import uuid
from decimal import Decimal

from django.utils import timezone

from .models import Order, OrderPayment


def record_captured_payment_for_test(order):
    token = uuid.uuid4().hex
    payment, _ = OrderPayment.objects.update_or_create(
        order=order,
        defaults={
            "amount": int(Decimal(order.total_amount) * 100),
            "shop_amount": int(Decimal(order.subtotal) * 100),
            "platform_fee": int(Decimal(order.application_fee) * 100),
            "currency": "INR",
            "provider_account_id": "acc_SyntheticShop",
            "key_id": "rzp_test_ledger",
            "provider_order_id": "order_" + token,
            "provider_payment_id": "pay_" + token,
            "provider_refund_id": None,
            "status": "captured",
            "captured_at": timezone.now(),
            "refunded_amount": 0,
            "refund_started_at": None,
            "last_error": "",
        },
    )
    Order.objects.filter(pk=order.pk).update(payment_status="paid")
    order.payment_status = "paid"
    return payment
