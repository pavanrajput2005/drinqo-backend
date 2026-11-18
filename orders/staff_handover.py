"""Authoritative outside-shop completion; no customer or staff OTP."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from customers.models import Notification
from shops.geofence import distance_between_coordinates
from .constants import order_requires_high_value_approval, get_high_value_threshold
from .locking import lock_order_with_shop
from .models import CustomerHandoverLocation, OrderPayment, StaffHandoverConfirmation
from .payments import verified_payment_exists
from .shop_eligibility import existing_order_can_continue

MAX_LOCATION_AGE = timedelta(seconds=60)
RADIUS_M = 100


class HandoverError(APIException):
    def __init__(self, code, detail, status_code=409, **extra):
        self.status_code = status_code
        self.detail = {"code": code, "detail": detail, **extra}


def fresh(captured_at, now):
    return -timedelta(seconds=5) <= now - captured_at <= MAX_LOCATION_AGE


def distance(shop, latitude, longitude):
    try:
        lat, lon = Decimal(str(shop.latitude)), Decimal(str(shop.longitude))
        if not lat.is_finite() or not lon.is_finite() or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        raise HandoverError("shop_location_unavailable", "A valid approved shop location is required.") from None
    return distance_between_coordinates(latitude, longitude, lat, lon)


def eligible(order):
    if order.status != "ready" or order.pickup_verified:
        raise HandoverError("order_not_ready", "Order must be ready and awaiting handover.")
    if order.pickup_mode not in {"walk_in", "vehicle"}:
        raise HandoverError("invalid_pickup_mode", "Order pickup mode requires review.")
    if order_requires_high_value_approval(order) or (
        order.total_amount >= get_high_value_threshold() and (
            order.super_admin_approved_by_id is None or order.super_admin_approved_by.user_type != "super_admin"
        )
    ):
        raise HandoverError("approval_required", "Recorded Super Admin approval is required.", 403)
    if not existing_order_can_continue(order):
        raise HandoverError("shop_restricted", "Shop eligibility or existing-order review does not permit completion.", 403)
    # Serialize against captured/refund-ledger updates as well as order writes.
    OrderPayment.objects.select_for_update().filter(order=order).first()
    if not verified_payment_exists(order):
        raise HandoverError("payment_required", "Verified captured payment is required.", 402)
    if order.pickup_mode == "vehicle" and (
        order.vehicle is None or order.vehicle.customer.user_id != order.customer_id
    ):
        raise HandoverError("invalid_vehicle", "The selected customer vehicle requires review.")


def result(order, record, replay=False):
    return {
        "order_id": order.pk, "status": "completed", "pickup_mode": record.pickup_mode,
        "pickup_verified": True, "pickup_verified_at": record.confirmed_at,
        "pickup_verified_by": record.actor_id, "handover_method": "staff_confirmation",
        "confirmation_id": record.pk, "already_completed": replay,
    }


@transaction.atomic
def confirm_staff_handover(user, order_id, data):
    if user.user_type != "shop_admin" or not user.is_active or user.is_blocked:
        raise PermissionDenied("Only the owning, active Shop Admin may confirm handover.")
    order = lock_order_with_shop(order_id, shop__owner_id=user.pk)
    if data["customer_id"] != order.customer_id:
        raise ValidationError({"customer_id": "Customer does not match this order."})
    previous = StaffHandoverConfirmation.objects.filter(order=order).first()
    if order.status == "completed" and order.pickup_verified and previous:
        return result(order, previous, replay=True)
    eligible(order)
    now = timezone.now()
    if not fresh(data["location_captured_at"], now):
        raise HandoverError("stale_staff_location", "Obtain a fresh foreground staff location.", 400)
    staff_distance = distance(order.shop, data["latitude"], data["longitude"])
    if staff_distance > RADIUS_M:
        raise HandoverError("outside_handover_radius", "Staff must be within 100 metres of the shop.", 400)
    customer_distance = None
    location_status = "unavailable"
    location = CustomerHandoverLocation.objects.select_for_update().filter(order=order).first()
    if location and location.latitude is not None:
        location_status = "stale"
        if fresh(location.captured_at, now) and fresh(location.received_at, now):
            customer_distance = distance(order.shop, location.latitude, location.longitude)
            if customer_distance > RADIUS_M:
                raise HandoverError("customer_outside_radius", "Fresh customer location is outside 100 metres.", 400)
            location_status = "fresh_reported"
    record = StaffHandoverConfirmation.objects.create(
        order=order, actor=user, pickup_mode=order.pickup_mode,
        customer_id_checked=order.customer_id,
        staff_distance_m=Decimal(str(staff_distance)).quantize(Decimal("0.01")),
        staff_location_captured_at=data["location_captured_at"],
        customer_distance_m=Decimal(str(customer_distance)).quantize(Decimal("0.01")) if customer_distance is not None else None,
        customer_location_status=location_status,
        acknowledgments={key: data[key] for key in (
            "customer_checked", "order_checked", "physically_handed_over", "outside_shop_confirmed",
        )},
    )
    order.pickup_verified, order.pickup_verified_at, order.pickup_verified_by = True, record.confirmed_at, user
    order.status = "completed"
    order.save(update_fields=["pickup_verified", "pickup_verified_at", "pickup_verified_by", "status", "updated_at"])
    Notification.objects.create(
        user=order.customer, order=order, notification_type="order_update", title="Order Completed",
        message=f"Your order #{order.pk} was handed over outside the shop" + (
            " on foot." if order.pickup_mode == "walk_in" else " at your selected vehicle."
        ),
    )
    return result(order, record)


@transaction.atomic
def report_customer_location(user, order_id, data):
    order = lock_order_with_shop(order_id, customer_id=user.pk)
    eligible(order)
    now = timezone.now()
    if not fresh(data["location_captured_at"], now):
        raise HandoverError("stale_customer_location", "Obtain a fresh foreground customer location.", 400)
    measured_distance = distance(order.shop, data["latitude"], data["longitude"])
    CustomerHandoverLocation.objects.update_or_create(order=order, defaults={
        "latitude": data["latitude"], "longitude": data["longitude"],
        "captured_at": data["location_captured_at"],
    })
    return {"order_id": order.pk, "within_radius": measured_distance <= RADIUS_M,
            "distance_to_shop_m": round(measured_distance, 2), "pickup_radius_m": RADIUS_M}
