from decimal import Decimal


HIGH_VALUE_THRESHOLD = Decimal("15000.00")
HIGH_VALUE_APPROVAL_PENDING_STATUSES = (
    "pending_super_admin",
    "pending",
    "admin_approved",
    "accepted",
    "confirmed",
    "preparing",
    "ready",
)


def get_high_value_threshold():
    return HIGH_VALUE_THRESHOLD


def order_requires_high_value_approval(order):
    return (
        order.total_amount >= get_high_value_threshold()
        and order.super_admin_approved_at is None
    )
