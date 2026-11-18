from shops.permissions import (
    latest_verified_license_document,
    shop_operation_block_reason,
)
from shop_admin.models import SuperAdminAuditLog


SHOP_REVIEW_APPROVAL_ACTION = "restricted_order_continuation_authorized"
SHOP_REVIEWABLE_REASONS = {"shop_suspended", "license_expired"}
REVIEWABLE_ORDER_STATUSES = {"accepted", "confirmed", "preparing", "ready"}


def shop_review_snapshot(shop):
    license_document = latest_verified_license_document(shop)
    latest_shop_audit = SuperAdminAuditLog.objects.filter(
        resource_type="shop",
        resource_id=shop.id,
    ).first()
    return {
        "shop_updated_at": shop.updated_at.isoformat(),
        "latest_shop_audit_id": (
            latest_shop_audit.id if latest_shop_audit is not None else None
        ),
        "license_status": shop.license_status,
        "is_active": shop.is_active,
        "is_suspended_by_super_admin": shop.is_suspended_by_super_admin,
        "license_document_id": (
            license_document.id if license_document is not None else None
        ),
        "license_expiry_date": (
            license_document.expiry_date.isoformat()
            if license_document is not None
            else None
        ),
    }


def order_requires_super_admin_review(order):
    return (
        order.status in REVIEWABLE_ORDER_STATUSES
        and shop_requires_super_admin_order_review(order.shop)
    )


def shop_requires_super_admin_order_review(shop):
    return shop_operation_block_reason(shop) in SHOP_REVIEWABLE_REASONS


def order_has_current_super_admin_review(order):
    if not order_requires_super_admin_review(order):
        return False

    approval = SuperAdminAuditLog.objects.filter(
        resource_type="order",
        resource_id=order.id,
        action=SHOP_REVIEW_APPROVAL_ACTION,
    ).first()
    return bool(
        approval
        and approval.metadata.get("shop_state") == shop_review_snapshot(order.shop)
    )


def existing_order_can_continue(order):
    reason = shop_operation_block_reason(order.shop)
    if reason is None:
        return True
    if reason not in SHOP_REVIEWABLE_REASONS:
        return False
    return order_has_current_super_admin_review(order)
