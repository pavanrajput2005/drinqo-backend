
import csv
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models.functions import TruncDate

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import JWTAuthentication
from customers.models import Notification, UserNotificationPreference, UserPushToken
from orders.models import Order, OrderItem
from orders.locking import lock_order_with_shop
from orders.shop_eligibility import (
    existing_order_can_continue,
    order_requires_super_admin_review,
    shop_requires_super_admin_order_review,
)
from orders.constants import (
    HIGH_VALUE_APPROVAL_PENDING_STATUSES,
    get_high_value_threshold,
    order_requires_high_value_approval,
)
from orders.serializers import OrderSerializer
from products.models import Product, StockMovement
from shops.permissions import (
    IsApprovedActiveShopAdmin,
    shop_can_perform_operations,
)
from shops.models import Shop
from shops.serializers import ShopAdminShopUpdateSerializer

from .api_serializers import ShopAdminProductSerializer
from .models import ShopAdminNotification
from .serializers import ShopAdminNotificationSerializer


# --------------------------------------------------
# COMMON SHOP ADMIN HELPERS
# --------------------------------------------------

def get_shop_admin_shop(user):
    """Return the main DRINQO shop owned by this authenticated user."""
    if getattr(user, "user_type", None) != "shop_admin":
        return None

    try:
        return Shop.objects.get(owner=user)
    except Shop.DoesNotExist:
        return None


def _shop_access_error(user):
    if getattr(user, "user_type", None) != "shop_admin":
        return Response(
            {"detail": "Shop Admin access required."},
            status=status.HTTP_403_FORBIDDEN,
        )

    return Response(
        {"detail": "No shop is assigned to this Shop Admin."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _get_owned_order(request, order_id):
    """Only return orders belonging to the authenticated Shop Admin's shop."""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return None, None, _shop_access_error(request.user)

    order = get_object_or_404(
        Order,
        id=order_id,
        shop=shop,
    )

    return shop, order, None


def _is_high_value_order(order):
    """Block shop decisions until the high-value order is approved."""
    return order_requires_high_value_approval(order)


ORDER_ACCEPTED_TITLE = "Order Accepted!"
ORDER_ACCEPTED_MESSAGE = (
    "Your order has been accepted. Our shop staff will hand over your order "
    "at your vehicle in approximately 10 minutes."
)
WALK_IN_ORDER_ACCEPTED_MESSAGE = (
    "Your order has been accepted. Please wait outside the shop within 100 metres. "
    "Our shop staff will come outside and hand over your order in approximately 10 minutes."
)


def _order_accepted_message(order):
    if order.pickup_mode == "walk_in":
        return WALK_IN_ORDER_ACCEPTED_MESSAGE
    return ORDER_ACCEPTED_MESSAGE


def _accept_locked_order(order, user):
    """Accept once; callers must lock and validate the order in an atomic block."""
    order.status = "confirmed"
    order.decided_at = timezone.now()
    order.decided_by = user
    order.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    return Notification.objects.create(
        user=order.customer,
        order=order,
        notification_type="order_update",
        title=ORDER_ACCEPTED_TITLE,
        message=_order_accepted_message(order),
    )


def _has_foreign_shop_item(order):
    for item in order.items.select_related("product").select_for_update():
        if item.product.shop_id != order.shop_id:
            return True
    return False


REJECT_MESSAGES = {
    "out_of_stock": "Sorry, we are unable to accept your order because the item is out of stock.",
    "shop_closed": "Sorry, we are unable to accept your order because the shop is closed.",
    "unable_to_fulfil": "Sorry, we are unable to accept your order at this time.",
    "other": "Sorry, we are unable to accept your order at this time.",
}


# --------------------------------------------------
# SHOP ADMIN PROFILE
# --------------------------------------------------

@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_me(request):
    """GET /api/shop-admin/me/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    return Response({
        "user_id": request.user.id,
        "email": getattr(request.user, "email", ""),
        "user_type": request.user.user_type,
        "shop": {
            "id": shop.id,
            "shop_name": shop.shop_name,
            "phone": shop.phone,
            "email": shop.email,
            "address_line1": shop.address_line1,
            "address_line2": shop.address_line2,
            "city": shop.city,
            "state": shop.state,
            "postal_code": shop.postal_code,
            "license_status": shop.license_status,
            "is_active": shop.is_active,
            "is_open": getattr(shop, "is_open", True),
            "opening_hours": getattr(shop, "opening_hours", {}),
            "latitude": (
                str(shop.latitude)
                if shop.latitude is not None else None
            ),
            "longitude": (
                str(shop.longitude)
                if shop.longitude is not None else None
            ),
        },
    })


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_update_shop(request):
    """PATCH /api/shop-admin/shop/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    allowed_fields = {
        "phone",
        "email",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "opening_hours",
        "is_open",
    }

    if not hasattr(request.data, "keys"):
        return Response(
            {"detail": "A JSON object is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    supplied_fields = set(request.data.keys())
    unknown_fields = supplied_fields - allowed_fields
    if unknown_fields:
        return Response(
            {
                "detail": "Unknown fields.",
                "fields": sorted(unknown_fields),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not supplied_fields:
        return Response(
            {
                "detail": "No editable fields supplied.",
                "editable_fields": sorted(allowed_fields),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = ShopAdminShopUpdateSerializer(
        shop,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    shop = serializer.save()

    return Response({
        "detail": "Shop information updated.",
        "shop_id": shop.id,
        "updated_fields": sorted(supplied_fields),
        "shop": {
            "latitude": str(shop.latitude) if shop.latitude is not None else None,
            "longitude": str(shop.longitude) if shop.longitude is not None else None,
        },
    })


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_update_shop_availability(request):
    """PATCH /api/shop-admin/shop/availability/"""
    if getattr(request.user, "user_type", None) != "shop_admin":
        return Response(
            {"detail": "Shop Admin access required."},
            status=status.HTTP_403_FORBIDDEN,
        )

    shop = get_shop_admin_shop(request.user)
    if shop is None:
        return Response(
            {"detail": "No shop is assigned to this Shop Admin."},
            status=status.HTTP_404_NOT_FOUND,
        )

    update_fields = []
    if "is_active" in request.data:
        is_active = request.data.get("is_active")
        if not isinstance(is_active, bool):
            return Response(
                {"detail": "Field 'is_active' is required and must be a boolean."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_active and shop.is_suspended_by_super_admin:
            return Response(
                {"detail": "Only a Super Admin can reactivate a suspended shop."},
                status=status.HTTP_403_FORBIDDEN,
            )
        shop.is_active = is_active
        update_fields.append("is_active")

    if "is_open" in request.data:
        is_open = request.data.get("is_open")
        if not isinstance(is_open, bool):
            return Response(
                {"detail": "Field 'is_open' must be a boolean."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shop.is_open = is_open
        update_fields.append("is_open")

    if not update_fields:
        return Response(
            {"detail": "Field 'is_active' or 'is_open' is required and must be a boolean."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    shop.save(update_fields=update_fields + ["updated_at"])

    return Response(
        {
            "message": "Shop availability updated successfully.",
            "shop": {
                "id": shop.id,
                "shop_name": shop.shop_name,
                "is_active": shop.is_active,
            },
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_inventory(request):
    """GET /api/shop-admin/inventory/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    products = Product.objects.filter(shop=shop).order_by("name")
    return Response(ShopAdminProductSerializer(products, many=True).data)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
@transaction.atomic
def shop_admin_adjust_inventory(request, product_id):
    """POST /api/shop-admin/inventory/<product_id>/adjust/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    product = get_object_or_404(
        Product.objects.select_for_update(),
        id=product_id,
        shop=shop,
    )

    quantity_change = request.data.get("quantity_change")
    if quantity_change is None:
        return Response(
            {"detail": "Field 'quantity_change' is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(quantity_change, bool) or not isinstance(quantity_change, (str, int)):
        return Response(
            {"detail": "Field 'quantity_change' must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        quantity_change = int(quantity_change)
    except (TypeError, ValueError):
        return Response(
            {"detail": "Field 'quantity_change' must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if product.stock_quantity + quantity_change < 0:
        return Response(
            {"detail": "Adjustment would make stock negative."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    previous_stock = product.stock_quantity
    note = request.data.get("reason", "manual")
    if not isinstance(note, str) or len(note) > 255:
        return Response({"reason": "Reason must be text of at most 255 characters."}, status=400)
    if previous_stock + quantity_change > 2147483647:
        return Response({"quantity_change": "Resulting stock exceeds the supported range."}, status=400)
    product.stock_quantity = previous_stock + quantity_change
    product.save(update_fields=["stock_quantity", "updated_at"])

    StockMovement.objects.create(
        product=product,
        changed_by=request.user,
        quantity_change=quantity_change,
        quantity_before=previous_stock,
        quantity_after=product.stock_quantity,
        reason="order_adjustment",
        note=note,
    )

    return Response(
        {
            "message": "Inventory updated successfully.",
            "product_id": product.id,
            "shop_id": shop.id,
            "quantity_change": quantity_change,
            "previous_stock": previous_stock,
            "stock_quantity": product.stock_quantity,
            "reason": request.data.get("reason", "manual"),
        },
        status=status.HTTP_201_CREATED,
    )


# --------------------------------------------------
# SHOP ADMIN DASHBOARD
# --------------------------------------------------

@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_dashboard(request):
    """GET /api/shop-admin/dashboard/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    products = Product.objects.filter(shop=shop)
    orders = (
        Order.objects.filter(shop=shop)
        .exclude(status="pending_super_admin")
        .exclude(
            status__in=HIGH_VALUE_APPROVAL_PENDING_STATUSES,
            total_amount__gte=get_high_value_threshold(),
            super_admin_approved_at__isnull=True,
        )
    )
    todays_orders = orders.filter(
        created_at__date=timezone.localdate()
    )

    summary = orders.aggregate(
        total_orders=Count("id"),
        total_product_value=Sum("subtotal"),
    )

    return Response({
        "shop_id": shop.id,
        "shop_name": shop.shop_name,
        "shop_is_active": shop.is_active,
        "is_open": getattr(shop, "is_open", True),
        "license_status": shop.license_status,
        "total_products": products.count(),
        "active_products": products.filter(is_active=True).count(),
        "total_orders": summary["total_orders"] or 0,
        "accepted_orders_count": orders.filter(status__in=["accepted", "confirmed"]).count(),
        "ready_orders_count": orders.filter(status="ready").count(),
        "new_orders_count": orders.filter(status__in=["pending", "admin_approved"]).count(),
        "todays_orders": todays_orders.count(),
        "total_product_value": str(
            summary["total_product_value"] or 0
        ),
    })


# --------------------------------------------------
# SHOP ADMIN NOTIFICATIONS
# --------------------------------------------------

@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_notifications(request):
    """GET /api/shop-admin/notifications/"""
    if getattr(request.user, "user_type", None) != "shop_admin":
        return _shop_access_error(request.user)

    notifications = ShopAdminNotification.objects.filter(
        shop_admin=request.user
    ).order_by("-created_at")

    return Response(
        ShopAdminNotificationSerializer(
            notifications,
            many=True,
        ).data
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_mark_notification_read(request, notification_id):
    """POST /api/shop-admin/notifications/<id>/read/"""
    if getattr(request.user, "user_type", None) != "shop_admin":
        return _shop_access_error(request.user)

    notification = get_object_or_404(
        ShopAdminNotification,
        id=notification_id,
        shop_admin=request.user,
    )

    notification.is_read = True
    notification.save(update_fields=["is_read"])

    return Response({
        "detail": "Notification marked as read.",
        "notification_id": notification.id,
        "is_read": notification.is_read,
    })


# --------------------------------------------------
# SHOP ADMIN PRODUCTS
# --------------------------------------------------

@api_view(["GET", "POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_products(request):
    """GET/POST /api/shop-admin/products/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    if request.method == "GET":
        products = Product.objects.filter(
            shop=shop
        ).order_by("-created_at")

        return Response(
            ShopAdminProductSerializer(
                products,
                many=True,
            ).data
        )

    if not shop.is_active or shop.license_status != "verified":
        return Response(
            {
                "detail": (
                    "Your shop must be active and its license "
                    "verified before managing products."
                )
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = ShopAdminProductSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(shop=shop)

    return Response(
        serializer.data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PATCH", "DELETE"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
@transaction.atomic
def shop_admin_product_detail(request, product_id):
    """GET/PATCH/DELETE a product belonging to this shop."""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    product = get_object_or_404(
        Product.objects.select_for_update() if request.method != "GET" else Product.objects.all(),
        id=product_id,
        shop=shop,
    )

    if request.method == "GET":
        return Response(
            ShopAdminProductSerializer(product).data
        )

    if not shop.is_active or shop.license_status != "verified":
        return Response(
            {"detail": "Shop is not active or license is not verified."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "DELETE":
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])

        return Response({
            "detail": "Product archived successfully."
        })

    serializer = ShopAdminProductSerializer(
        product,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save(shop=shop)

    return Response(serializer.data)


# --------------------------------------------------
# SHOP ADMIN ORDERS - LIST
# --------------------------------------------------

@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def shop_admin_orders(request):
    """GET /api/shop-admin/orders/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    orders = (
        Order.objects
        .filter(shop=shop)
        .exclude(status="pending_super_admin")
        .exclude(
            status__in=HIGH_VALUE_APPROVAL_PENDING_STATUSES,
            total_amount__gte=get_high_value_threshold(),
            super_admin_approved_at__isnull=True,
        )
        .prefetch_related("items")
        .order_by("-created_at")
    )

    if not shop_can_perform_operations(shop):
        orders = [
            order
            for order in orders
            if existing_order_can_continue(order)
        ]
        if not orders:
            return Response(
                {
                    "detail": (
                        "No existing orders are authorized to continue "
                        "under Super Admin review."
                    ),
                    "review_required": shop_requires_super_admin_order_review(
                        shop
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

    return Response(
        OrderSerializer(orders, many=True).data
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def shop_admin_order_detail(request, order_id):
    """GET /api/shop-admin/orders/<id>/"""
    shop = get_shop_admin_shop(request.user)
    if shop is None:
        return _shop_access_error(request.user)

    order = (
        Order.objects.select_related("customer", "shop", "vehicle")
        .prefetch_related("items")
        .filter(id=order_id, shop=shop)
        .first()
    )
    if not shop_can_perform_operations(shop) and (
        order is None or not existing_order_can_continue(order)
    ):
        return Response(
            {
                "detail": (
                    "This order is unavailable until Super Admin review."
                ),
                "review_required": (
                    order is not None
                    and order_requires_super_admin_review(order)
                ),
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    if order is None:
        raise NotFound("Order not found.")
    if _is_high_value_order(order):
        raise NotFound("This order is awaiting Super Admin review.")
    return Response(OrderSerializer(order).data)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_order_decision(request, order_id):
    """POST /api/shop-admin/orders/<id>/decision/"""
    decision = request.data.get("decision")
    reason = request.data.get("reason_code", "")

    if decision not in ("accepted", "rejected"):
        return Response({"decision": ["Choose accepted or rejected."]}, status=status.HTTP_400_BAD_REQUEST)
    if decision == "rejected" and (not isinstance(reason, str) or reason not in REJECT_MESSAGES):
        return Response({"reason_code": ["Choose a valid rejection reason code."]}, status=status.HTTP_400_BAD_REQUEST)
    if decision == "accepted" and reason:
        return Response({"detail": "reason_code is only allowed when rejecting."}, status=status.HTTP_400_BAD_REQUEST)

    shop = get_shop_admin_shop(request.user)
    if shop is None:
        return _shop_access_error(request.user)

    with transaction.atomic():
        order = lock_order_with_shop(
            order_id, shop__owner_id=request.user.id,
        )

        if order.status not in {"pending", "admin_approved"}:
            return Response({"detail": "This order cannot be decided in its current status."}, status=status.HTTP_409_CONFLICT)
        if order_requires_high_value_approval(order):
            return Response({"detail": "Super Admin approval is required before this order can be decided."}, status=status.HTTP_409_CONFLICT)

        if decision == "accepted":
            if _has_foreign_shop_item(order):
                return Response({"detail": "Order contains an item outside the assigned shop."}, status=status.HTTP_409_CONFLICT)
            customer_notification = _accept_locked_order(order, request.user)
            message = _order_accepted_message(order)
        else:
            for item in order.items.select_related("product"):
                product = Product.objects.select_for_update().get(pk=item.product_id)
                before = product.stock_quantity
                product.stock_quantity += item.quantity
                product.save(update_fields=["stock_quantity", "updated_at"])
                StockMovement.objects.create(
                    product=product,
                    changed_by=request.user,
                    quantity_change=item.quantity,
                    quantity_before=before,
                    quantity_after=product.stock_quantity,
                    reason="order_adjustment",
                    note=f"Stock returned after order #{order.id} was rejected",
                )
            order.status = "rejected"
            order.reject_reason_code = reason
            title = "Order update"
            message = REJECT_MESSAGES[reason]

            order.decided_at = timezone.now()
            order.decided_by = request.user
            order.save(update_fields=["status", "reject_reason_code", "decided_at", "decided_by", "updated_at"])
            customer_notification = Notification.objects.create(
                user=order.customer,
                order=order,
                notification_type="order_update",
                title=title,
                message=message,
            )

    return Response(
        {
            "id": order.id,
            "order_number": f"DRQ-{order.id:04d}",
            "status": order.status,
            "decision": decision,
            "reason_code": reason if decision == "rejected" else None,
            "notification_created": bool(customer_notification.pk),
            "customer_message": message,
            "decided_at": order.decided_at,
        },
        status=status.HTTP_200_OK,
    )


# --------------------------------------------------
# ACCEPT ORDER
# --------------------------------------------------

@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_accept_order(request, order_id):
    """POST /api/shop-admin/orders/<id>/accept/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order_id,
            shop=shop,
        )

        if _is_high_value_order(order):
            return Response(
                {
                    "detail": (
                        "This order requires Super Admin approval "
                        "and cannot be accepted here."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if order.status not in {"pending", "admin_approved"}:
            return Response(
                {
                    "detail": "Only pending or admin-approved orders can be accepted.",
                    "current_status": order.status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if _has_foreign_shop_item(order):
            return Response(
                {"detail": "Order contains an item outside the assigned shop."},
                status=status.HTTP_409_CONFLICT,
            )
        _accept_locked_order(order, request.user)

    return Response({
        "detail": "Order accepted successfully.",
        "order_id": order.id,
        "status": order.status,
        "message": _order_accepted_message(order),
    })


# --------------------------------------------------
# REJECT ORDER
# --------------------------------------------------

@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_reject_order(request, order_id):
    """POST /api/shop-admin/orders/<id>/reject/"""
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order_id,
            shop=shop,
        )

        if _is_high_value_order(order):
            return Response(
                {
                    "detail": (
                        "This order requires Super Admin review "
                        "and cannot be rejected here."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if order.status != "pending":
            return Response(
                {
                    "detail": "Only pending orders can be rejected.",
                    "current_status": order.status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        items = OrderItem.objects.filter(
            order=order
        ).select_related("product")

        for item in items:
            product = Product.objects.select_for_update().get(
                id=item.product_id
            )
            product.stock_quantity += item.quantity
            product.save(update_fields=["stock_quantity", "updated_at"])

        order.status = "cancelled"
        order.save(update_fields=["status", "updated_at"])

        Notification.objects.create(
            user=order.customer,
            order=order,
            notification_type="order_update",
            title="Order Rejected",
            message=(
                "Your order was rejected because stock is unavailable. "
                "Reason: Stock unavailable."
            ),
        )

    return Response({
        "detail": "Order rejected successfully.",
        "order_id": order.id,
        "status": order.status,
        "reason": "Stock unavailable",
    })


# --------------------------------------------------
# KEEP ORDER PENDING FOR HANDOVER
# --------------------------------------------------

@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_keep_order_pending(request, order_id):
    """
    POST /api/shop-admin/orders/<id>/pending/

    The order stays confirmed. No database status change is made.
    """
    shop = get_shop_admin_shop(request.user)

    if shop is None:
        return _shop_access_error(request.user)

    order = get_object_or_404(
        Order,
        id=order_id,
        shop=shop,
    )

    if _is_high_value_order(order):
        return Response(
            {
                "detail": (
                    "This order requires Super Admin approval."
                )
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    if order.status != "confirmed":
        return Response(
            {
                "detail": (
                    "Only accepted orders can remain pending for handover."
                ),
                "current_status": order.status,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response({
        "detail": "Order is still awaiting handover.",
        "order_id": order.id,
        "status": order.status,
    })

# --------------------------------------------------
# UPDATE ORDER STATUS: PREPARING / READY
# --------------------------------------------------

@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def shop_admin_update_order_status(request, order_id):
    """POST /api/shop-admin/orders/<id>/status/"""

    if getattr(request.user, "user_type", None) != "shop_admin":
        return _shop_access_error(request.user)

    new_status = request.data.get("status")

    allowed_transitions = {
        "confirmed": "preparing",
        "preparing": "ready",
    }

    if new_status not in ("preparing", "ready"):
        return Response(
            {"detail": "Allowed statuses are preparing and ready."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        shop = Shop.objects.select_for_update().filter(
            owner=request.user
        ).first()
        if shop is None:
            return _shop_access_error(request.user)

        order = Order.objects.select_for_update().filter(
            id=order_id,
            shop=shop,
        ).first()

        if not shop_can_perform_operations(shop) and (
            order is None or not existing_order_can_continue(order)
        ):
            return Response(
                {
                    "detail": (
                        "This shop cannot update orders until it is "
                        "operational or the order is approved by Super Admin."
                    ),
                    "review_required": (
                        order is not None
                        and order_requires_super_admin_review(order)
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if order is None:
            raise NotFound("Order not found.")

        expected_status = next(
            (
                current
                for current, target in allowed_transitions.items()
                if target == new_status
            ),
            None,
        )

        # Historical accepted records may progress explicitly without a data rewrite.
        legacy_preparing = order.status == "accepted" and new_status == "preparing"
        if order.status != expected_status and not legacy_preparing:
            return Response(
                {
                    "detail": "Invalid order status transition.",
                    "current_status": order.status,
                    "requested_status": new_status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if _is_high_value_order(order):
            return Response(
                {
                    "detail": (
                        "This order requires Super Admin approval "
                        "and cannot be processed here."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        from orders.payments import verified_payment_exists
        if not verified_payment_exists(order):
            return Response(
                {"code": "payment_required", "detail": "Verified customer payment is required before preparing the order."},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        order.status = new_status
        order.save(update_fields=["status", "updated_at"])

        Notification.objects.create(
            user=order.customer,
            order=order,
            notification_type="order_update",
            title="Order Update",
            message=(
                "Your order is being prepared."
                if new_status == "preparing"
                else "Your order is ready. Please wait outside the shop within 100 metres for staff handover."
            ),
        )

    return Response({
        "detail": "Order status updated successfully.",
        "order_id": order.id,
        "status": order.status,
    })


@api_view(["POST", "DELETE"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_product_image(request, product_id):
    shop = get_shop_admin_shop(request.user)
    if shop is None:
        return _shop_access_error(request.user)
    product = get_object_or_404(Product, id=product_id, shop=shop)
    if not shop.is_active or shop.license_status != "verified":
        return Response(
            {"detail": "Shop is not active or license is not verified."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "DELETE":
        if product.image:
            product.image.delete(save=False)
            product.image = None
            product.save(update_fields=["image", "updated_at"])
        return Response(
            {"product_id": product.id, "image": None},
            status=status.HTTP_200_OK,
        )

    image = request.FILES.get("image")
    if image is None:
        return Response(
            {"image": "This field is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if image.size > 5 * 1024 * 1024:
        return Response(
            {"image": "Image must be 5 MB or smaller."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = ShopAdminProductSerializer(
        product,
        data={"image": image},
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_bulk_import_products(request):
    shop = get_shop_admin_shop(request.user)
    if shop is None:
        return _shop_access_error(request.user)
    if not shop.is_active or shop.license_status != "verified":
        return Response(
            {"detail": "Shop is not active or license is not verified."},
            status=status.HTTP_403_FORBIDDEN,
        )

    upload = request.FILES.get("file")
    if upload is None:
        return Response(
            {"file": "Upload a CSV file in the 'file' field."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if upload.size > 2 * 1024 * 1024:
        return Response(
            {"file": "CSV file must be 2 MB or smaller."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        reader = csv.DictReader(TextIOWrapper(upload.file, encoding="utf-8-sig"))
        headers = set(reader.fieldnames or [])
        required_headers = {"name", "category", "price", "stock_quantity"}
        missing_headers = sorted(required_headers - headers)
        if missing_headers:
            return Response(
                {"detail": "Required CSV columns are missing.", "columns": missing_headers},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error):
        return Response(
            {"file": "CSV file could not be parsed as UTF-8."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if len(rows) > 500:
        return Response(
            {"file": "CSV import is limited to 500 products."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    valid_categories = {choice[0] for choice in Product.CATEGORY_CHOICES}
    errors = []
    products = []
    for row_number, row in enumerate(rows, start=2):
        name = (row.get("name") or "").strip()
        category = (row.get("category") or "").strip().lower()
        brand = (row.get("brand") or "").strip()
        description = (row.get("description") or "").strip()
        try:
            price = Decimal((row.get("price") or "").strip())
            quantity_text = (row.get("stock_quantity") or "").strip()
            quantity = int(quantity_text)
            threshold_text = (row.get("low_stock_threshold") or "5").strip()
            threshold = int(threshold_text)
        except (InvalidOperation, ValueError):
            errors.append({
                "row": row_number,
                "detail": "price, stock_quantity, and low_stock_threshold must be valid numbers.",
            })
            continue
        if not price.is_finite():
            errors.append({
                "row": row_number,
                "errors": {"price": "Price must be a finite decimal amount."},
            })
            continue
        row_errors = {}
        if not name:
            row_errors["name"] = "This field is required."
        elif len(name) > 200:
            row_errors["name"] = "Must be 200 characters or fewer."
        if category not in valid_categories:
            row_errors["category"] = "Unsupported product category."
        if len(brand) > 150:
            row_errors["brand"] = "Must be 150 characters or fewer."
        if price <= 0:
            row_errors["price"] = "Price must be greater than zero."
        elif price.adjusted() > 7 or max(-price.as_tuple().exponent, 0) > 2:
            row_errors["price"] = "Price supports up to 8 digits and 2 decimal places."
        if quantity < 0:
            row_errors["stock_quantity"] = "Stock quantity cannot be negative."
        elif quantity > 2147483647:
            row_errors["stock_quantity"] = "Stock quantity exceeds the supported range."
        if threshold < 0:
            row_errors["low_stock_threshold"] = "Threshold cannot be negative."
        elif threshold > 2147483647:
            row_errors["low_stock_threshold"] = "Threshold exceeds the supported range."
        if row_errors:
            errors.append({"row": row_number, "errors": row_errors})
            continue
        products.append(Product(
            shop=shop,
            name=name,
            category=category,
            brand=brand,
            description=description,
            price=price,
            stock_quantity=quantity,
            low_stock_threshold=threshold,
        ))

    if errors:
        return Response(
            {"detail": "CSV validation failed; no products were imported.", "errors": errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not products:
        return Response(
            {"detail": "CSV contains no product rows."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    with transaction.atomic():
        Product.objects.bulk_create(products)
    return Response(
        {"created_count": len(products), "products": ShopAdminProductSerializer(
            products,
            many=True,
            context={"request": request},
        ).data},
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_reports(request):
    shop = get_shop_admin_shop(request.user)
    if shop is None:
        return _shop_access_error(request.user)

    report_range = request.query_params.get("range", "7d")
    day_counts = {"7d": 7, "30d": 30, "90d": 90}
    if report_range not in day_counts:
        return Response(
            {"range": "Allowed ranges are 7d, 30d, and 90d."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    india_timezone = ZoneInfo("Asia/Kolkata")
    today = timezone.localdate(timezone=india_timezone)
    start_day = today - timedelta(days=day_counts[report_range] - 1)
    start = datetime.combine(start_day, time.min, tzinfo=india_timezone)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=india_timezone)
    orders = Order.objects.filter(
        shop=shop,
        created_at__gte=start,
        created_at__lt=end,
    ).exclude(status__in=["pending_super_admin", "cancelled", "rejected"]).exclude(
        status__in=HIGH_VALUE_APPROVAL_PENDING_STATUSES,
        total_amount__gte=get_high_value_threshold(),
        super_admin_approved_at__isnull=True,
    )
    top_products = (
        OrderItem.objects.filter(order__in=orders)
        .values("product_id", "product_name")
        .annotate(quantity=Sum("quantity"), sales=Sum("subtotal"))
        .order_by("-quantity", "-sales", "product_name")[:10]
    )
    return Response({
        "range": report_range,
        "orders_count": orders.count(),
        "sales": format(orders.aggregate(total=Sum("subtotal"))["total"] or Decimal(0), ".2f"),
        "order_trend": list(
            orders.annotate(day=TruncDate("created_at", tzinfo=india_timezone))
            .values("day")
            .annotate(orders=Count("id"), sales=Sum("subtotal"))
            .order_by("day")
        ),
        "top_products": [
            {
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "quantity": item["quantity"],
                "sales": format(item["sales"] or Decimal(0), ".2f"),
            }
            for item in top_products
        ],
    })


@api_view(["GET", "PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_notification_preferences(request):
    if request.user.user_type != "shop_admin":
        return _shop_access_error(request.user)
    preferences, _ = UserNotificationPreference.objects.get_or_create(
        user=request.user,
    )
    from customers.serializers import UserNotificationPreferenceSerializer

    if request.method == "GET":
        return Response(UserNotificationPreferenceSerializer(preferences).data)
    serializer = UserNotificationPreferenceSerializer(
        preferences,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(["POST", "DELETE"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_push_token(request):
    if request.user.user_type != "shop_admin":
        return _shop_access_error(request.user)
    from customers.serializers import UserPushTokenSerializer
    from customers.push_tokens import register_push_token

    if request.method == "POST":
        serializer = UserPushTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device, created = register_push_token(
            user=request.user,
            token=serializer.validated_data["token"],
            platform=serializer.validated_data["platform"],
        )
        return Response(
            {"id": device.id, "platform": device.platform, "registered": True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    token = request.data.get("token")
    if not isinstance(token, str) or not token.strip():
        return Response(
            {"token": "This field is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    deleted, _ = UserPushToken.objects.filter(
        user=request.user,
        token=token,
    ).delete()
    if not deleted:
        return Response(status=status.HTTP_404_NOT_FOUND)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsApprovedActiveShopAdmin])
def shop_admin_mark_all_notifications_read(request):
    if request.user.user_type != "shop_admin":
        return _shop_access_error(request.user)
    updated = ShopAdminNotification.objects.filter(
        shop_admin=request.user,
        is_read=False,
    ).update(is_read=True)
    return Response({"updated_count": updated})
