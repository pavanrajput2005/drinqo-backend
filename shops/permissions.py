from django.db.models import OuterRef, Q, Subquery
from rest_framework.permissions import BasePermission
from django.utils import timezone

from .models import Shop, ShopLicenseDocument


def latest_verified_license_document(shop):
    return shop.application_documents.filter(
        verification_status="verified",
    ).order_by("-created_at", "-id").first()


def shop_operation_block_reason(shop):
    if shop.license_status in {"pending", "rejected"}:
        return "shop_not_approved"
    if shop.is_suspended_by_super_admin:
        return "shop_suspended"
    if shop.license_status == "expired":
        return "license_expired"
    if shop.license_status != "verified":
        return "shop_not_approved"
    if not shop.is_active:
        return "shop_inactive"

    license_document = latest_verified_license_document(shop)
    if license_document is None:
        return "license_document_missing"
    if license_document.expiry_date <= timezone.localdate():
        return "license_expired"
    return None


def shop_can_perform_operations(shop):
    return shop_operation_block_reason(shop) is None


def shop_can_accept_new_orders(shop):
    return shop.is_open and shop_can_perform_operations(shop)


def shops_accepting_new_orders():
    latest_verified_expiry = Subquery(
        ShopLicenseDocument.objects.filter(
            shop_id=OuterRef("pk"),
            verification_status="verified",
        )
        .order_by("-created_at", "-id")
        .values("expiry_date")[:1]
    )
    return (
        Shop.objects.filter(
            license_status="verified",
            is_active=True,
            is_open=True,
            is_suspended_by_super_admin=False,
        )
        .annotate(_latest_verified_license_expiry=latest_verified_expiry)
        .filter(_latest_verified_license_expiry__gt=timezone.localdate())
    )


def shops_requiring_super_admin_review():
    latest_verified_expiry = Subquery(
        ShopLicenseDocument.objects.filter(
            shop_id=OuterRef("pk"),
            verification_status="verified",
        )
        .order_by("-created_at", "-id")
        .values("expiry_date")[:1]
    )
    return (
        Shop.objects.annotate(
            _latest_verified_license_expiry=latest_verified_expiry
        )
        .filter(
            Q(license_status="expired")
            | Q(
                license_status="pending",
                _latest_verified_license_expiry__lte=timezone.localdate(),
            )
            | Q(
                license_status="verified",
                is_suspended_by_super_admin=True,
            )
            | Q(
                license_status="verified",
                is_active=True,
                is_suspended_by_super_admin=False,
                _latest_verified_license_expiry__lte=timezone.localdate(),
            )
        )
    )


class IsApprovedActiveShopAdmin(BasePermission):
    message = (
        "An approved, active shop with a current verified license is "
        "required for this operation."
    )

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "user_type", None) != "shop_admin":
            return False

        shop = (
            Shop.objects.filter(owner=user)
            .prefetch_related("application_documents")
            .first()
        )
        return bool(shop and shop_can_perform_operations(shop))
