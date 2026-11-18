"""Razorpay payment ledger. All provider HTTP calls take place outside row locks.

No caller-supplied amount, shop account, or payment status is trusted.
This integration is deliberately restricted to Test Mode until merchant approval.
"""
import hashlib
import json
import logging
import re
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from customers.checkout_eligibility import customer_checkout_eligibility
from customers.models import Notification

from .constants import order_requires_high_value_approval
from .locking import lock_order_with_shop
from .models import OrderPayment, PaymentOperationalCase, PaymentRefund, PaymentWebhookEvent, ShopPaymentAccount
from .razorpay_gateway import (
    GatewayError, RazorpayGateway, parse_payment, verify_checkout_signature, verify_webhook,
)
from .shop_eligibility import existing_order_can_continue

logger = logging.getLogger(__name__)
TERMINAL = {"cancelled", "rejected"}


class PaymentError(GatewayError):
    pass


def payment_projection(order):
    """Safe customer-facing ledger state; no provider IDs, signatures, or PII."""
    try:
        payment = order.payment
    except OrderPayment.DoesNotExist:
        payment = None
    eligible = False
    if order.status in {"confirmed", "accepted"} and (payment is None or payment.status in {"creating", "created"}):
        try:
            _check_payable(order, order.customer)
            gateway = RazorpayGateway()  # Configuration check only; no HTTP on reads.
            if payment is None:
                eligible = ShopPaymentAccount.objects.filter(shop_id=order.shop_id, is_enabled=True).exists()
            else:
                _check_snapshot(payment, order, gateway)
                eligible = True
        except GatewayError:
            pass
    if payment is None:
        return {
            "ledger_status": None, "refund_status": None,
            "reconciliation_required": False, "payment_eligible": eligible,
            "next_action": ("complete_payment" if eligible else "await_shop_confirmation"
                            if order.status in {"pending", "pending_super_admin", "admin_approved"} else "await_order_eligibility"),
        }
    reconciliation_required = payment.status == "review" or payment.operational_cases.filter(
        kind="reconciliation", status__in={"open", "review"}
    ).exists()
    refund_status = None
    latest_refund = payment.refunds.order_by("-created_at", "-id").first()
    if latest_refund:
        refund_status = latest_refund.status
    elif payment.status in {"refund_required", "refunding", "refunded"}:
        refund_status = payment.status
    eligible = eligible and not reconciliation_required
    if payment.status == "captured":
        next_action = "await_shop_preparation"
    elif refund_status in {"requested", "pending", "review", "failed"}:
        next_action = "refund_under_review"
    elif reconciliation_required:
        next_action = "payment_under_review"
    elif eligible:
        next_action = "complete_payment"
    else:
        next_action = "await_order_eligibility"
    return {
        "ledger_status": payment.status,
        "refund_status": refund_status,
        "reconciliation_required": reconciliation_required,
        "payment_eligible": eligible,
        "next_action": next_action,
    }


def open_operational_case(payment, kind, reason_code, *, actor=None, details=None):
    """Idempotently retain a nonsecret operator work item."""
    case, _ = PaymentOperationalCase.objects.get_or_create(
        payment=payment, kind=kind, reason_code=reason_code,
        status__in=["open", "review"],
        defaults={"opened_by": actor, "details": details or {}},
    )
    return case


def request_refund_record(payment, amount, *, actor=None, reason_code="terminal_order_refund"):
    """Create/reuse a refund request while preventing aggregate over-refund."""
    if type(amount) is not int or amount <= 0:
        raise PaymentError("invalid_refund_amount", "Invalid refund amount.", 400)
    existing = PaymentRefund.objects.select_for_update().filter(payment=payment, amount=amount).first()
    if existing:
        return existing
    committed_or_pending = PaymentRefund.objects.select_for_update().filter(
        payment=payment, status__in={"requested", "pending", "processed"}
    ).aggregate(total=Sum("amount"))["total"] or 0
    if committed_or_pending + amount > payment.amount:
        raise PaymentError("refund_exceeds_capture", "Refund requires reconciliation.", 409)
    return PaymentRefund.objects.create(
        payment=payment, amount=amount, requested_by=actor, reason_code=reason_code,
    )


def _provider_id(value, prefix):
    return isinstance(value, str) and len(value) <= 64 and re.fullmatch(prefix + r"[A-Za-z0-9]+", value) is not None


def paise(value):
    amount = Decimal(value) * 100
    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
        raise PaymentError("invalid_order_amount", "Order amount requires review.", 409)
    return int(amount)


def verified_payment_exists(order):
    """An old/manual `paid` flag alone cannot authorize preparation or handover."""
    return order.payment_status == "paid" and OrderPayment.objects.filter(
        order=order, status="captured", captured_at__isnull=False,
        provider_payment_id__isnull=False, provider_order_id__isnull=False,
        refunded_amount=0, amount=paise(order.total_amount),
        shop_amount=paise(order.subtotal), platform_fee=paise(order.application_fee),
    ).exclude(provider_payment_id="").exclude(provider_order_id="").exists()


def _check_payable(order, user):
    if order.status not in {"confirmed", "accepted"}:
        raise PaymentError("order_not_payable", "The shop must confirm the order before payment.", 409)
    if order_requires_high_value_approval(order):
        raise PaymentError("approval_required", "Super Admin approval is required before payment.", 403)
    if not existing_order_can_continue(order):
        raise PaymentError("shop_restricted", "This order requires Super Admin shop review.", 403)
    _profile, eligibility_failure = customer_checkout_eligibility(user)
    if eligibility_failure is not None:
        raise PaymentError(
            eligibility_failure.code,
            eligibility_failure.detail,
            eligibility_failure.status_code,
        )


def _check_snapshot(payment, order, gateway):
    if (
        payment.key_id != gateway.key_id or payment.currency != "INR"
        or payment.amount != paise(order.total_amount)
        or payment.shop_amount != paise(order.subtotal)
        or payment.platform_fee != paise(order.application_fee)
    ):
        raise PaymentError("payment_configuration_changed", "Payment reconciliation is required.", 409)


def _public(order, payment, *, checkout=False):
    data = {
        "order_id": order.pk, "payment_status": order.payment_status,
        "payment_state": payment.status, "gateway": "razorpay", "mode": "test",
        "amount": payment.amount, "currency": payment.currency,
        "platform_fee": f"{Decimal(payment.platform_fee) / 100:.2f}",
    }
    if checkout and payment.status == "created" and order.status in {"confirmed", "accepted"}:
        data["checkout"] = {
            "key": payment.key_id, "order_id": payment.provider_order_id,
            "amount": payment.amount, "currency": payment.currency,
            "name": "DRINQO",
        }
    return data


def _validate_order(entity, payment):
    if (
        not isinstance(entity, dict)
        or not _provider_id(entity.get("id"), "order_")
        or entity.get("receipt") != payment.pk.hex
        or type(entity.get("amount")) is not int or entity["amount"] != payment.amount
        or entity.get("currency") != "INR"
        or entity.get("partial_payment", False) is not False
    ):
        raise PaymentError("provider_order_mismatch", "Provider order requires reconciliation.", 502)


def initiate_payment(user, order_id):
    gateway = RazorpayGateway()
    with transaction.atomic():
        order = lock_order_with_shop(order_id, customer=user)
        _check_payable(order, user)
        payment = OrderPayment.objects.select_for_update().filter(order=order).first()
        if payment:
            _check_snapshot(payment, order, gateway)
            if payment.status in {"created", "captured"}:
                return _public(order, payment, checkout=True), 200
            # A CREATE may have succeeded remotely before the connection failed.
            # Never send a second CREATE for this order, even when a search is empty.
            recover = payment.status in {"creating", "review"} and not payment.provider_order_id
            if not recover:
                raise PaymentError("payment_unavailable", "Payment reconciliation is required.", 409)
        else:
            account = ShopPaymentAccount.objects.filter(shop=order.shop, is_enabled=True).first()
            if not account:
                raise PaymentError("shop_payments_unavailable", "Shop payment onboarding is incomplete.", 503)
            payment = OrderPayment.objects.create(
                order=order, amount=paise(order.total_amount), shop_amount=paise(order.subtotal),
                platform_fee=paise(order.application_fee),
                provider_account_id=account.provider_account_id, key_id=gateway.key_id,
            )
            recover = False
        payment_id = payment.pk

    try:
        entity = gateway.find_order(payment_id.hex) if recover else gateway.create_order(
            payment_id.hex, payment.amount, payment.shop_amount, payment.provider_account_id,
        )
        if entity is None:
            raise PaymentError("creation_outcome_unknown", "Payment initialization requires reconciliation.", 409)
        _validate_order(entity, payment)
        if recover:
            recovered = dict(entity, transfers=gateway.fetch_order_transfers(entity["id"]))
            gateway.validate_order_split(recovered, payment.shop_amount, payment.provider_account_id)
    except GatewayError:
        with transaction.atomic():
            lock_order_with_shop(order_id, customer=user)
            OrderPayment.objects.filter(pk=payment_id, provider_order_id__isnull=True).update(
                status="review", last_error="creation_outcome_unknown", updated_at=timezone.now(),
            )
        raise

    with transaction.atomic():
        order = lock_order_with_shop(order_id, customer=user)
        payment = OrderPayment.objects.select_for_update().get(pk=payment_id)
        _check_snapshot(payment, order, gateway)
        if payment.provider_order_id and payment.provider_order_id != entity["id"]:
            raise PaymentError("provider_order_mismatch", "Payment reconciliation is required.", 409)
        payment.provider_order_id = entity["id"]
        if payment.status == "creating" or (
            payment.status == "review" and payment.last_error == "creation_outcome_unknown"
        ):
            payment.status = "created"
            payment.last_error = ""
        payment.save(update_fields=["provider_order_id", "status", "last_error", "updated_at"])
        # Cancellation may have won while the provider request was in flight.
        if order.status not in {"confirmed", "accepted"}:
            return _public(order, payment), 409
        _check_payable(order, user)
        return _public(order, payment, checkout=True), 200 if recover else 201


def _validate_capture(entity, payment):
    if (
        not isinstance(entity, dict)
        or not _provider_id(entity.get("id"), "pay_")
        or entity.get("order_id") != payment.provider_order_id
        or type(entity.get("amount")) is not int or entity["amount"] != payment.amount
        or entity.get("currency") != "INR"
        or entity.get("status") not in ("captured", "refunded") or entity.get("captured") is not True
        or type(entity.get("amount_refunded", 0)) is not int
        or not 0 <= entity.get("amount_refunded", 0) <= payment.amount
        or (entity.get("status") == "refunded" and entity.get("amount_refunded") != payment.amount)
    ):
        raise PaymentError("payment_not_captured", "A matching captured payment is required.", 409)


def _apply_capture(order, payment, entity):
    """Called while shop/order/payment are locked. Notification failures roll back."""
    _validate_capture(entity, payment)
    if payment.provider_payment_id and payment.provider_payment_id != entity["id"]:
        raise PaymentError("payment_identity_conflict", "Payment reconciliation is required.", 409)
    first_capture = payment.captured_at is None
    payment.provider_payment_id = entity["id"]
    payment.captured_at = payment.captured_at or timezone.now()
    payment.refunded_amount = max(payment.refunded_amount, entity.get("amount_refunded", 0))
    # A replayed capture must never resurrect a refunded/refunding payment.
    if payment.refunded_amount == payment.amount:
        payment.status = "refunded"
        order.payment_status = "refunded"
    elif payment.refunded_amount:
        payment.status = "review"
        payment.last_error = "partial_refund_requires_review"
    elif payment.status == "review" and payment.last_error in {
        "partial_refund_requires_review", "refund_requires_review",
    }:
        pass
    elif payment.status not in {"refunded", "refunding", "refund_required"}:
        payment.status = "refund_required" if order.status in TERMINAL else "captured"
        order.payment_status = "paid"
    payment.save(update_fields=[
        "provider_payment_id", "captured_at", "refunded_amount", "status", "last_error", "updated_at",
    ])
    order.save(update_fields=["payment_status", "updated_at"])
    if first_capture:
        Notification.objects.create(
            user=order.customer, order=order, notification_type="payment", title="Payment Received",
            message=("Payment received for your order. A refund is required because the order was cancelled."
                     if order.status in TERMINAL else (
                         "Payment received. Your shop can now prepare your order."
                         if payment.status == "captured" else "Payment received. Payment reconciliation is required."
                     )),
        )


def _apply_failed_payment(order, payment, entity):
    """Record a signed failed attempt without changing paid/fulfillment state."""
    failed = parse_payment(entity)
    if (
        failed.get("status") != "failed"
        or failed.get("captured") is not False
        or failed.get("order_id") != payment.provider_order_id
        or failed.get("amount") != payment.amount
    ):
        raise PaymentError("invalid_webhook", "Invalid webhook.", 400)
    # A later failure event must never downgrade an already captured/refunded
    # payment. A created order remains usable for a customer retry in Razorpay.
    if payment.status == "created":
        payment.last_error = "payment_failed"
        payment.save(update_fields=["last_error", "updated_at"])


@sensitive_variables()
def verify_customer_payment(user, order_id, provider_order_id, payment_id, signature):
    gateway = RazorpayGateway()
    # No locks are held while validating the provider's current payment/order.
    payment = OrderPayment.objects.select_related("order").filter(order_id=order_id, order__customer=user).first()
    if payment is None:
        raise PaymentError("payment_not_found", "Payment not found.", 404)
    _check_snapshot(payment, payment.order, gateway)
    if provider_order_id != payment.provider_order_id or not verify_checkout_signature(
        payment.provider_order_id, payment_id, signature,
    ):
        raise PaymentError("invalid_payment_signature", "Invalid payment verification.", 400)
    entity = gateway.fetch_payment(payment_id)
    _validate_capture(entity, payment)
    remote_order = gateway.fetch_order(payment.provider_order_id)
    _validate_order(remote_order, payment)
    if remote_order.get("status") != "paid" or remote_order.get("amount_paid") != payment.amount:
        raise PaymentError("payment_not_captured", "Payment is still being confirmed.", 409)
    with transaction.atomic():
        order = lock_order_with_shop(order_id, customer=user)
        payment = OrderPayment.objects.select_for_update().get(pk=payment.pk)
        _check_snapshot(payment, order, gateway)
        _apply_capture(order, payment, entity)
        data = _public(order, payment)
    if payment.status == "refund_required":
        try_refund_order(order_id)
    return data, 200


def reconcile_payment(user, order_id):
    """Authenticated recovery when checkout's success callback never reached Django."""
    gateway = RazorpayGateway()
    payment = OrderPayment.objects.select_related("order").filter(order_id=order_id, order__customer=user).first()
    if payment is None:
        raise PaymentError("payment_not_found", "Payment not found.", 404)
    _check_snapshot(payment, payment.order, gateway)
    if not payment.provider_order_id:
        # Recovery must also work after cancellation. Never create a new charge.
        entity = gateway.find_order(payment.pk.hex)
        if entity is None:
            raise PaymentError("creation_outcome_unknown", "Payment initialization requires reconciliation.", 409)
        _validate_order(entity, payment)
        recovered = dict(entity, transfers=gateway.fetch_order_transfers(entity["id"]))
        gateway.validate_order_split(recovered, payment.shop_amount, payment.provider_account_id)
        with transaction.atomic():
            order = lock_order_with_shop(order_id, customer=user)
            payment = OrderPayment.objects.select_for_update().get(pk=payment.pk)
            _check_snapshot(payment, order, gateway)
            if payment.provider_order_id and payment.provider_order_id != entity["id"]:
                raise PaymentError("provider_order_mismatch", "Payment reconciliation is required.", 409)
            payment.provider_order_id = entity["id"]
            if payment.status == "creating" or (
                payment.status == "review" and payment.last_error == "creation_outcome_unknown"
            ):
                payment.status = "created"
                payment.last_error = ""
            payment.save(update_fields=["provider_order_id", "status", "last_error", "updated_at"])
    entities = gateway.fetch_order_payments(payment.provider_order_id)
    captures = [entity for entity in entities if entity.get("status") in {"captured", "refunded"}]
    if len(captures) > 1:
        raise PaymentError("multiple_captures", "Payment reconciliation is required.", 409)
    with transaction.atomic():
        order = lock_order_with_shop(order_id, customer=user)
        payment = OrderPayment.objects.select_for_update().get(pk=payment.pk)
        _check_snapshot(payment, order, gateway)
        if captures:
            _apply_capture(order, payment, captures[0])
        data = _public(order, payment)
    if payment.status in {"refund_required", "refunding"}:
        try_refund_order(order_id)
    return data, 200


def _apply_refund(order, payment, entity):
    if (
        not isinstance(entity, dict)
        or not _provider_id(entity.get("id"), "rfnd_")
        or not _provider_id(entity.get("payment_id"), "pay_")
        or entity.get("payment_id") != payment.provider_payment_id
        or type(entity.get("amount")) is not int or not 0 < entity["amount"] <= payment.amount
        or entity.get("currency") != "INR"
        or entity.get("status") not in ("pending", "processed", "failed")
    ):
        raise PaymentError("refund_mismatch", "Refund reconciliation is required.", 409)
    if payment.status == "refunded" and (
        payment.provider_refund_id or entity["status"] != "processed" or entity["amount"] != payment.amount
    ):
        return
    if entity["amount"] != payment.amount or entity["status"] == "failed":
        payment.status = "review"
        payment.last_error = "refund_requires_review"
        if entity["status"] == "processed":
            # A lower bound only for unexpected partial refunds; never increment
            # on replay. Full reconciliation must resolve the aggregate amount.
            payment.refunded_amount = max(payment.refunded_amount, entity["amount"])
    else:
        if payment.provider_refund_id and payment.provider_refund_id != entity["id"]:
            raise PaymentError("refund_identity_conflict", "Refund reconciliation is required.", 409)
        payment.provider_refund_id = entity["id"]
        payment.status = "refunded" if entity["status"] == "processed" else "refunding"
        if payment.status == "refunded":
            payment.refunded_amount = payment.amount
            order.payment_status = "refunded"
            order.save(update_fields=["payment_status", "updated_at"])
            Notification.objects.create(
                user=order.customer, order=order, notification_type="payment", title="Refund Processed",
                message="The payment for your order has been refunded. Bank processing times may vary.",
            )
    payment.save(update_fields=["provider_refund_id", "status", "refunded_amount", "last_error", "updated_at"])


def process_refund(order_id, *, gateway=None, actor=None, review_lookup=False):
    gateway = gateway or RazorpayGateway()
    with transaction.atomic():
        order = lock_order_with_shop(order_id)
        payment = OrderPayment.objects.select_for_update().filter(order=order).first()
        recovering_review = bool(review_lookup and payment and payment.status == "review"
                                 and payment.last_error == "refund_requires_review" and payment.refund_started_at)
        if payment is None or (payment.status not in {"refund_required", "refunding"} and not recovering_review):
            return
        _check_snapshot(payment, order, gateway)
        if not payment.provider_payment_id or order.status not in TERMINAL:
            raise PaymentError("refund_requires_review", "Refund reconciliation is required.", 409)
        recover = payment.refund_started_at is not None
        refund = request_refund_record(payment, payment.amount, actor=actor)
        if refund.status == "processed":
            return
        if not recovering_review:
            payment.status = "refunding"
        payment.refund_started_at = payment.refund_started_at or timezone.now()
        payment.save(update_fields=["status", "refund_started_at", "updated_at"])
        if not recovering_review:
            refund.status = "pending"
            refund.attempted_at = refund.attempted_at or timezone.now()
            refund.save(update_fields=["status", "attempted_at", "updated_at"])
    # The persisted start marker prevents a retry from issuing a second refund.
    if recover:
        refunds = gateway.fetch_refunds(payment.provider_payment_id)
        matches = [item for item in refunds if item.get("receipt") == payment.pk.hex or (
            payment.provider_refund_id and item.get("id") == payment.provider_refund_id
        )]
        if len(matches) != 1:
            raise PaymentError("refund_outcome_unknown", "Refund reconciliation is required.", 409)
        entity = matches[0]
    else:
        entity = gateway.refund(payment.provider_payment_id, payment.amount, payment.pk.hex)
    with transaction.atomic():
        order = lock_order_with_shop(order_id)
        payment = OrderPayment.objects.select_for_update().get(pk=payment.pk)
        _apply_refund(order, payment, entity)
        refund = PaymentRefund.objects.select_for_update().get(payment=payment, amount=payment.amount)
        refund.provider_refund_id = entity.get("id") or refund.provider_refund_id
        refund.status = (entity["status"] if entity["status"] in {"pending", "processed"}
                         and entity["amount"] == payment.amount else "review")
        refund.confirmed_at = timezone.now() if refund.status == "processed" else None
        refund.save(update_fields=["provider_refund_id", "status", "confirmed_at", "updated_at"])
        if refund.status == "review":
            open_operational_case(payment, "refund", "refund_requires_review", actor=actor)


def try_refund_order(order_id, *, gateway=None, actor=None):
    """A committed cancellation stays committed if the gateway is unavailable.

    The durable ledger remains refund_required/refunding. A subsequent customer
    reconciliation (or approved operator retry) reconciles the same refund.
    """
    try:
        process_refund(order_id, gateway=gateway, actor=actor)
    except Exception:
        # Do not log exception/request objects: they may hold authorization data.
        with transaction.atomic():
            payment = OrderPayment.objects.select_for_update().filter(order_id=order_id).first()
            if payment is not None and payment.status in {"refund_required", "refunding"}:
                payment.status = "review"
                payment.last_error = "refund_requires_review"
                payment.save(update_fields=["status", "last_error", "updated_at"])
                open_operational_case(payment, "refund", "refund_requires_review", actor=actor)
        logger.warning("Payment refund requires reconciliation for order %s", order_id)


def reconcile_operational_payment(payment, *, gateway=None, actor=None):
    """Operator-safe reconciliation entry point; never fabricates capture/refund success."""
    gateway = gateway or RazorpayGateway()
    with transaction.atomic():
        payment = OrderPayment.objects.select_for_update().get(pk=payment.pk)
        if payment.status in {"refund_required", "refunding"} or (
            payment.status == "review" and payment.last_error == "refund_requires_review" and payment.refund_started_at
        ):
            order_id = payment.order_id
        elif payment.status == "review":
            open_operational_case(payment, "reconciliation", "payment_review_required", actor=actor)
            return payment
        else:
            return payment
    process_refund(order_id, gateway=gateway, actor=actor, review_lookup=True)
    return OrderPayment.objects.get(pk=payment.pk)


@sensitive_variables()
def handle_webhook(raw_body, signature, event_id):
    if not isinstance(raw_body, bytes) or len(raw_body) > 65536:
        raise PaymentError("invalid_webhook", "Invalid webhook.", 400)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id or "") or not verify_webhook(raw_body, signature):
        raise PaymentError("invalid_webhook_signature", "Invalid webhook verification.", 400)
    try:
        event = json.loads(raw_body)
        event_type = event["event"]
        if not isinstance(event_type, str):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise PaymentError("invalid_webhook", "Invalid webhook.", 400) from None
    if event_type not in {
        "payment.captured", "payment.failed", "order.paid",
        "refund.processed", "refund.failed",
    }:
        return {"received": True}, 200
    try:
        key = "refund" if event_type.startswith("refund.") else "payment"
        entity = event["payload"][key]["entity"]
        if not isinstance(entity, dict):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PaymentError("invalid_webhook", "Invalid webhook.", 400) from None
    binding = entity.get("payment_id") if key == "refund" else entity.get("order_id")
    prefix = "pay_" if key == "refund" else "order_"
    if not _provider_id(binding, prefix):
        raise PaymentError("invalid_webhook", "Invalid webhook.", 400)
    queryset = OrderPayment.objects.filter(
        **({"provider_payment_id": binding} if key == "refund" else {"provider_order_id": binding}),
    )
    # Unknown events may be for other products/merchants; do not persist their PII.
    hint = queryset.only("order_id").first()
    recovered_capture = None
    if hint is None and key == "refund":
        # Refund delivery may precede capture delivery. Resolve the payment's
        # authenticated provider order outside locks; never forget an already
        # processed refund while waiting for the capture webhook to retry.
        recovered_capture = RazorpayGateway().fetch_payment(binding)
        hint = OrderPayment.objects.filter(
            provider_order_id=recovered_capture.get("order_id"),
        ).only("order_id").first()
    if hint is None:
        raise PaymentError("payment_not_found", "Payment mapping not found; retry later.", 503)
    digest = hashlib.sha256(raw_body).hexdigest()
    with transaction.atomic():
        order = lock_order_with_shop(hint.order_id)
        payment = OrderPayment.objects.select_for_update().get(order=order)
        _check_snapshot(payment, order, RazorpayGateway())
        previous = PaymentWebhookEvent.objects.filter(event_id=event_id).first()
        if previous:
            if previous.payment_id != payment.pk or previous.payload_digest != digest:
                raise PaymentError("webhook_identity_conflict", "Webhook reconciliation is required.", 400)
            return {"received": True}, 200
        if key == "refund":
            if recovered_capture is not None:
                _apply_capture(order, payment, recovered_capture)
            _apply_refund(order, payment, entity)
        elif event_type == "payment.failed":
            _apply_failed_payment(order, payment, entity)
        else:
            _apply_capture(order, payment, entity)
        PaymentWebhookEvent.objects.create(
            event_id=event_id, payload_digest=digest, event_type=event_type, payment=payment,
        )
    if payment.status == "refund_required":
        try_refund_order(order.pk)
    return {"received": True}, 200
