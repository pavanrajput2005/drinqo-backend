from orders.locking import lock_order_with_shop
import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import JWTAuthentication
from customers.age_verification import (
    AGE_PROOF_DOCUMENT_TYPE,
    is_age_verified,
    is_at_least_minimum_age,
)
from customers.models import (
    CustomerVerificationSession,
    LegalPolicy,
    Notification,
    SupportTicket,
)
from customers.serializers import (
    CustomerVerificationSessionSerializer,
    LegalPolicyAdminSerializer,
    LegalPolicySerializer,
    SupportTicketSerializer,
)
from orders.models import Order, OrderItem, OrderPayment, PaymentOperationalCase, PaymentRefund
from orders.payments import payment_projection, reconcile_operational_payment
from orders.razorpay_gateway import GatewayError
from orders.constants import (
    HIGH_VALUE_APPROVAL_PENDING_STATUSES,
    HIGH_VALUE_THRESHOLD,
    get_high_value_threshold,
)
from orders.shop_eligibility import (
    REVIEWABLE_ORDER_STATUSES,
    SHOP_REVIEW_APPROVAL_ACTION,
    SHOP_REVIEWABLE_REASONS,
    order_has_current_super_admin_review,
    shop_review_snapshot,
)
from products.models import Product
from shops.models import Shop, ShopLicenseDocument, ShopLocationChangeRequest
from shops.permissions import (
    shop_operation_block_reason,
    shops_requiring_super_admin_review,
)
from shops.serializers import (
    ShopLicenseDocumentSerializer as ShopApplicationLicenseDocumentSerializer,
)

from .models import (
    PlatformSettings,
    ShopAdminNotification,
    SuperAdminAuditLog,
)


User = get_user_model()
INDIA_TIME_ZONE = ZoneInfo("Asia/Kolkata")
_JWT_PATTERN = re.compile(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|token|secret|api[_-]?key|private[_-]?key|"
    r"authorization|service[_-]?account|firebase[_-]?uid|"
    r"upi[_-]?(id|address)|card[_-]?(number|details)|account[_-]?number)"
    r"\b\s*(?:[:=]|\bis\b)\s*\S+"
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SENSITIVE_NUMBER_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){10,19}(?!\d)")


class IsSuperAdmin(BasePermission):
    message = "Super Admin access required."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.user_type == "super_admin"
        )


def _audit(actor, action, resource_type, resource_id, summary, metadata=None):
    SuperAdminAuditLog.objects.create(
        actor=actor,
        actor_email=actor.email,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=_safe_text(summary),
        metadata=metadata or {},
    )


def _safe_text(value):
    value = _SECRET_PATTERN.sub("[REDACTED]", value)
    value = _JWT_PATTERN.sub("[REDACTED]", value)
    value = _EMAIL_PATTERN.sub("[REDACTED]", value)
    return _SENSITIVE_NUMBER_PATTERN.sub("[REDACTED]", value)[:500]


def _validate_payload(request, allowed_fields, required_fields=()):
    if not isinstance(request.data, Mapping):
        raise ValidationError({"detail": "A JSON object is required."})

    supplied_fields = set(request.data.keys())
    unknown_fields = supplied_fields - set(allowed_fields)
    if unknown_fields:
        raise ValidationError(
            {"detail": "Unknown fields.", "fields": sorted(unknown_fields)}
        )

    missing_fields = set(required_fields) - supplied_fields
    if missing_fields:
        raise ValidationError(
            {"detail": "Required fields are missing.", "fields": sorted(missing_fields)}
        )

    return request.data


def _required_text(data, field, max_length=500):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({field: "This field is required."})
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError(
            {field: f"Ensure this field has no more than {max_length} characters."}
        )
    return value


def _page_url(request, page_number):
    query = request.query_params.copy()
    query["page"] = str(page_number)
    return request.build_absolute_uri(f"{request.path}?{query.urlencode()}")


def _paginated_response(request, queryset, serialize):
    try:
        page_number = int(request.query_params.get("page", "1"))
        page_size = int(request.query_params.get("page_size", "20"))
    except (TypeError, ValueError):
        raise ValidationError({"detail": "page and page_size must be integers."})

    if page_number < 1:
        raise ValidationError({"page": "Page must be at least 1."})
    if page_size < 1 or page_size > 100:
        raise ValidationError({"page_size": "Page size must be between 1 and 100."})

    count = queryset.count()
    page_count = (count + page_size - 1) // page_size
    if page_number > max(page_count, 1):
        raise ValidationError({"page": "That page contains no results."})

    offset = (page_number - 1) * page_size
    results = [serialize(item) for item in queryset[offset:offset + page_size]]
    return Response(
        {
            "count": count,
            "next": (
                _page_url(request, page_number + 1)
                if page_number < page_count else None
            ),
            "previous": (
                _page_url(request, page_number - 1)
                if page_number > 1 else None
            ),
            "results": results,
        }
    )


def _history(resource_type, resource_id, include_metadata=False):
    history = []
    for item in SuperAdminAuditLog.objects.filter(
        resource_type=resource_type,
        resource_id=resource_id,
    ):
        entry = {
            "id": item.id,
            "actor_id": item.actor_id,
            "actor_email": item.actor_email,
            "action": item.action,
            "summary": item.summary,
            "created_at": item.created_at,
        }
        if include_metadata:
            entry["metadata"] = item.metadata
        history.append(entry)
    return history


def _masked_phone(phone):
    digits = "".join(character for character in phone if character.isdigit())
    if not digits:
        return ""
    return f"******{digits[-4:]}"


def _masked_email(email):
    if "@" not in email:
        return ""
    local_part, domain = email.split("@", 1)
    return f"{local_part[:1]}***@{domain}"


def _shop_summary(shop):
    owner = shop.owner
    return {
        "id": shop.id,
        "shop_name": shop.shop_name,
        "city": shop.city,
        "state": shop.state,
        "is_active": shop.is_active,
        "is_suspended_by_super_admin": shop.is_suspended_by_super_admin,
        "license_status": shop.license_status,
        "owner": {
            "id": owner.id,
            "name": owner.get_full_name() or owner.username,
            "email": owner.email,
        },
    }


def _application_summary(shop, request=None):
    documents = ShopApplicationLicenseDocumentSerializer(
        shop.application_documents.all(),
        many=True,
        context={"request": request},
    ).data
    return {
        "id": shop.id,
        "shop_name": shop.shop_name,
        "owner_name": shop.owner.get_full_name() or shop.owner.username,
        "owner_email": shop.owner.email,
        "phone": shop.phone,
        "city": shop.city,
        "state": shop.state,
        "license_number": shop.license_number,
        "license_status": shop.license_status,
        "application_status": shop.license_status,
        "submitted_at": shop.created_at,
        "documents": documents,
    }


def _pending_license_document_summary(document, request):
    """Serialize a review queue item without exposing file storage paths."""
    shop = document.shop
    data = ShopApplicationLicenseDocumentSerializer(
        document,
        context={"request": request},
    ).data
    data["shop"] = {
        "id": shop.id,
        "shop_name": shop.shop_name,
        "license_status": shop.license_status,
        "is_active": shop.is_active,
        "is_suspended_by_super_admin": shop.is_suspended_by_super_admin,
    }
    return data


def _shop_query(request):
    queryset = (
        Shop.objects.select_related("owner")
        .prefetch_related("application_documents")
        .order_by("-created_at", "-id")
    )
    application_status = request.query_params.get("status", "pending")
    if application_status != "all":
        valid_statuses = {choice[0] for choice in Shop.LICENSE_STATUS_CHOICES}
        if application_status not in valid_statuses:
            raise ValidationError({"status": "Unsupported application status."})
        queryset = queryset.filter(license_status=application_status)

    search = request.query_params.get("search", "").strip()
    if search:
        queryset = queryset.filter(
            models_q_shop(search)
        )
    return queryset


def models_q_shop(search):
    from django.db.models import Q

    return (
        Q(shop_name__icontains=search)
        | Q(owner__email__icontains=search)
        | Q(owner__first_name__icontains=search)
        | Q(owner__last_name__icontains=search)
        | Q(city__icontains=search)
        | Q(state__icontains=search)
        | Q(license_number__icontains=search)
    )


def _customer_query(request):
    queryset = User.objects.filter(user_type="customer").order_by("-date_joined", "-id")
    customer_status = request.query_params.get("status")
    if customer_status == "active":
        queryset = queryset.filter(is_active=True, is_blocked=False)
    elif customer_status == "blocked":
        queryset = queryset.filter(is_blocked=True)
    elif customer_status == "inactive":
        queryset = queryset.filter(is_active=False)
    elif customer_status not in (None, "", "all"):
        raise ValidationError({"status": "Use active, blocked, inactive, or all."})

    search = request.query_params.get("search", "").strip()
    if search:
        from django.db.models import Q

        queryset = queryset.filter(
            Q(email__icontains=search)
            | Q(phone__icontains=search)
            | Q(username__icontains=search)
        )
    return queryset


def _customer_summary(user):
    return {
        "id": user.id,
        "email": _masked_email(user.email),
        "phone_masked": _masked_phone(user.phone),
        "is_active": user.is_active,
        "is_blocked": user.is_blocked,
        "age_verified": is_age_verified(user),
        "order_count": user.orders.count(),
        "created_at": user.date_joined,
    }


def _order_summary(order):
    return {
        "id": order.id,
        "order_number": str(order.id),
        "status": order.status,
        "shop": {
            "id": order.shop_id,
            "shop_name": order.shop.shop_name,
        },
        "customer": {
            "id": order.customer_id,
            "email": _masked_email(order.customer.email),
            "phone_masked": _masked_phone(order.customer.phone),
        },
        "subtotal": str(order.subtotal),
        "application_fee": str(order.application_fee),
        "platform_fee": str(order.application_fee),
        "total_amount": str(order.total_amount),
        "payment_status": order.payment_status,
        "created_at": order.created_at,
    }


def _high_value_orders(request):
    order_status = request.query_params.get("status", "pending")
    queryset = Order.objects.filter(
        total_amount__gte=get_high_value_threshold()
    )
    if order_status == "pending":
        queryset = queryset.filter(
            status__in=HIGH_VALUE_APPROVAL_PENDING_STATUSES,
            super_admin_approved_at__isnull=True,
        )
    elif order_status == "all":
        pass
    elif order_status in dict(Order.STATUS_CHOICES):
        queryset = queryset.filter(status=order_status)
    else:
        raise ValidationError({"status": "Unsupported order status."})
    return queryset.select_related("shop", "customer").order_by(
        "-created_at", "-id"
    )


def _local_day_bounds(day):
    start = datetime.combine(day, time.min, tzinfo=INDIA_TIME_ZONE)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=INDIA_TIME_ZONE)
    return start, end


def _date_filter(value, field_name, is_end=False):
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError({field_name: "Use YYYY-MM-DD format."})
    boundary_time = time.max if is_end else time.min
    return datetime.combine(parsed, boundary_time, tzinfo=INDIA_TIME_ZONE)


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_me(request):
    return Response(
        {
            "id": request.user.id,
            "email": request.user.email,
            "user_type": request.user.user_type,
            "is_active": request.user.is_active,
        }
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_dashboard(request):
    today = timezone.localdate(timezone=INDIA_TIME_ZONE)
    start, end = _local_day_bounds(today)
    today_orders = Order.objects.filter(created_at__gte=start, created_at__lt=end)

    return Response(
        {
            "active_shops": Shop.objects.filter(
                is_active=True,
                license_status="verified",
            ).count(),
            "pending_shop_applications": Shop.objects.filter(
                license_status="pending"
            ).count(),
            "suspended_shops": Shop.objects.filter(
                is_suspended_by_super_admin=True,
            ).count(),
            "today_orders": today_orders.count(),
            "today_gmv": str(today_orders.aggregate(total=Sum("subtotal"))["total"] or Decimal("0.00")),
            "high_value_orders_pending": Order.objects.filter(
                status__in=HIGH_VALUE_APPROVAL_PENDING_STATUSES,
                total_amount__gte=get_high_value_threshold(),
                super_admin_approved_at__isnull=True,
            ).count(),
            "unread_notifications": Notification.objects.filter(
                user=request.user,
                is_read=False,
            ).count(),
        }
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_notifications(request):
    queryset = Notification.objects.filter(user=request.user).select_related("order")

    def serialize(notification):
        order = notification.order
        return {
            "id": notification.id,
            "notification_type": notification.notification_type,
            "type": notification.notification_type,
            "title": notification.title,
            "message": notification.message,
            "resource_type": "order" if order else None,
            "resource_id": order.id if order else None,
            "is_read": notification.is_read,
            "created_at": notification.created_at,
        }

    return _paginated_response(request, queryset, serialize)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_mark_notification_read(request, notification_id):
    notification = get_object_or_404(
        Notification,
        id=notification_id,
        user=request.user,
    )
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return Response({"id": notification.id, "is_read": True})


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_payments(request):
    queryset = Order.objects.select_related("shop").order_by("-created_at", "-id")

    payment_status = request.query_params.get("payment_status")
    if payment_status:
        valid_statuses = {choice[0] for choice in Order.PAYMENT_STATUS_CHOICES}
        if payment_status not in valid_statuses:
            raise ValidationError({"payment_status": "Unsupported payment status."})
        queryset = queryset.filter(payment_status=payment_status)

    for parameter, field in (("order_id", "id"), ("shop_id", "shop_id")):
        value = request.query_params.get(parameter)
        if value:
            try:
                value = int(value)
            except ValueError:
                raise ValidationError({parameter: "Must be an integer."})
            if value < 1:
                raise ValidationError({parameter: "Must be at least 1."})
            queryset = queryset.filter(**{field: value})

    from_date = _date_filter(request.query_params.get("from"), "from")
    to_date = _date_filter(request.query_params.get("to"), "to", is_end=True)
    if from_date and to_date and from_date > to_date:
        raise ValidationError({"from": "Must not be later than to."})
    if from_date:
        queryset = queryset.filter(created_at__gte=from_date)
    if to_date:
        queryset = queryset.filter(created_at__lte=to_date)

    search = request.query_params.get("search", "").strip()
    if search:
        search_filter = Q(shop__shop_name__icontains=search)
        if search.isdigit() and len(search) <= 10:
            search_filter |= Q(id=int(search))
        queryset = queryset.filter(search_filter)

    return _paginated_response(
        request,
        queryset,
        lambda order: {
            "id": order.id,
            "order_id": order.id,
            "order_number": str(order.id),
            "shop": {
                "id": order.shop_id,
                "shop_name": order.shop.shop_name,
            },
            "amount": str(order.total_amount),
            "payment_status": order.payment_status,
            "payment_method": order.payment_method,
            "created_at": order.created_at,
        },
    )


def _payment_detail(payment):
    order = payment.order
    return {
        "payment_id": str(payment.pk),
        "order_id": order.id,
        "shop_id": order.shop_id,
        "currency": payment.currency,
        "amount": payment.amount,
        "shop_amount": payment.shop_amount,
        "platform_fee": payment.platform_fee,
        "ledger_status": payment.status,
        "order_payment_status": order.payment_status,
        "refunds": [
            {"id": str(refund.pk), "amount": refund.amount, "status": refund.status,
             "reason_code": refund.reason_code, "created_at": refund.created_at}
            for refund in payment.refunds.all()
        ],
        "cases": [
            {"id": case.id, "kind": case.kind, "status": case.status,
             "reason_code": case.reason_code, "created_at": case.created_at}
            for case in payment.operational_cases.all()
        ],
        "projection": payment_projection(order),
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_payment_detail(request, payment_id):
    payment = get_object_or_404(
        OrderPayment.objects.select_related("order", "order__shop").prefetch_related("refunds", "operational_cases"),
        pk=payment_id,
    )
    return Response(_payment_detail(payment))


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_payment_cases(request):
    kind = request.query_params.get("kind")
    status_value = request.query_params.get("status")
    queryset = PaymentOperationalCase.objects.select_related("payment", "payment__order").order_by("-created_at", "-id")
    if kind:
        if kind not in dict(PaymentOperationalCase.KIND_CHOICES):
            raise ValidationError({"kind": "Unsupported case kind."})
        queryset = queryset.filter(kind=kind)
    if status_value:
        if status_value not in dict(PaymentOperationalCase.STATUS_CHOICES):
            raise ValidationError({"status": "Unsupported case status."})
        queryset = queryset.filter(status=status_value)
    return _paginated_response(request, queryset, lambda case: {
        "id": case.id, "payment_id": str(case.payment_id), "order_id": case.payment.order_id,
        "kind": case.kind, "status": case.status, "reason_code": case.reason_code,
        "created_at": case.created_at,
    })


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_create_payment_case(request, payment_id):
    data = _validate_payload(
        request, {"kind", "reason_code", "external_reference"},
        required_fields=("kind", "reason_code"),
    )
    kind = data["kind"]
    reason_code = data["reason_code"]
    reference = data.get("external_reference", "")
    if kind not in dict(PaymentOperationalCase.KIND_CHOICES):
        raise ValidationError({"kind": "Unsupported case kind."})
    if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z0-9_]{3,64}", reason_code):
        raise ValidationError({"reason_code": "Use lowercase letters, digits, and underscores."})
    if not isinstance(reference, str) or len(reference) > 64:
        raise ValidationError({"external_reference": "Must be a string of 64 characters or fewer."})
    with transaction.atomic():
        payment = get_object_or_404(OrderPayment.objects.select_for_update(), pk=payment_id)
        case, created = PaymentOperationalCase.objects.get_or_create(
            payment=payment, kind=kind, reason_code=reason_code,
            status__in=["open", "review"],
            defaults={"external_reference": reference, "opened_by": request.user},
        )
        _audit(request.user, "payment_case_opened", "order", payment.order_id,
               "Opened a payment operational case.",
               {"payment_id": str(payment.pk), "kind": kind, "reason_code": reason_code})
    return Response({"id": case.id, "kind": case.kind, "status": case.status, "created": created}, status=201 if created else 200)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_retry_payment_refund(request, payment_id):
    payment = get_object_or_404(OrderPayment.objects.select_related("order"), pk=payment_id)
    if payment.status not in {"refund_required", "refunding", "review"}:
        raise ValidationError({"detail": "Payment is not awaiting refund review."})
    with transaction.atomic():
        payment = OrderPayment.objects.select_for_update().get(pk=payment.pk)
        PaymentOperationalCase.objects.get_or_create(
            payment=payment, kind="refund", reason_code="operator_refund_retry",
            status__in=["open", "review"], defaults={"opened_by": request.user},
        )
        _audit(request.user, "payment_refund_retry_requested", "order", payment.order_id,
               "Requested a payment refund reconciliation retry.", {"order_id": payment.order_id})
    # The gateway remains disabled by default. This call is injectable in tests;
    # it never treats a request as a successful refund without provider evidence.
    try:
        reconcile_operational_payment(payment, actor=request.user)
    except GatewayError as exc:
        return Response({"code": exc.code, "detail": exc.detail}, status=exc.status_code)
    payment.refresh_from_db()
    return Response(_payment_detail(OrderPayment.objects.select_related("order", "order__shop").prefetch_related("refunds", "operational_cases").get(pk=payment.pk)))


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_shop_applications(request):
    queryset = _shop_query(request)
    return _paginated_response(
        request,
        queryset,
        lambda shop: _application_summary(shop, request),
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_pending_license_documents(request):
    """List pending license reviews for every shop, including active shops."""
    queryset = (
        ShopLicenseDocument.objects.filter(verification_status="pending")
        .select_related("shop", "shop__owner")
        .order_by("created_at", "id")
    )
    return _paginated_response(
        request,
        queryset,
        lambda document: _pending_license_document_summary(document, request),
    )


def _location_request_summary(item):
    return {
        "id": item.id,
        "status": item.status,
        "shop": {
            "id": item.shop_id,
            "shop_name": item.shop.shop_name,
            "latitude": str(item.shop.latitude) if item.shop.latitude is not None else None,
            "longitude": str(item.shop.longitude) if item.shop.longitude is not None else None,
        },
        "latitude": str(item.latitude),
        "longitude": str(item.longitude),
        "previous_latitude": str(item.previous_latitude) if item.previous_latitude is not None else None,
        "previous_longitude": str(item.previous_longitude) if item.previous_longitude is not None else None,
        "reason": item.reason,
        "review_reason": item.review_reason or None,
        "requested_by_id": item.requested_by_id,
        "reviewed_by_id": item.reviewed_by_id,
        "created_at": item.created_at,
        "reviewed_at": item.reviewed_at,
    }


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_location_requests(request):
    requested_status = request.query_params.get("status", "pending")
    valid_statuses = {choice[0] for choice in ShopLocationChangeRequest.STATUS_CHOICES}
    if requested_status not in valid_statuses | {"all"}:
        raise ValidationError({"status": "Unsupported location request status."})
    queryset = ShopLocationChangeRequest.objects.select_related("shop", "requested_by", "reviewed_by")
    if requested_status != "all":
        queryset = queryset.filter(status=requested_status)
    search = request.query_params.get("search", "").strip()
    if search:
        queryset = queryset.filter(Q(shop__shop_name__icontains=search) | Q(shop__city__icontains=search))
    return _paginated_response(request, queryset, _location_request_summary)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_review_location_request(request, request_id):
    data = _validate_payload(request, {"decision", "reason"}, required_fields=("decision",))
    decision = data["decision"]
    if decision not in {"approve", "reject"}:
        raise ValidationError({"decision": "Choose approve or reject."})
    reason = data.get("reason", "")
    if not isinstance(reason, str):
        raise ValidationError({"reason": "Must be a string."})
    reason = reason.strip()
    if len(reason) > 500:
        raise ValidationError({"reason": "Ensure this field has no more than 500 characters."})
    reason = _safe_text(reason) if reason else ""

    with transaction.atomic():
        # Consistent lock order: shop first, then location request.
        candidate = get_object_or_404(ShopLocationChangeRequest.objects.select_related("shop"), pk=request_id)
        shop = Shop.objects.select_for_update().get(pk=candidate.shop_id)
        item = ShopLocationChangeRequest.objects.select_for_update().select_related("shop").get(pk=request_id)
        if item.status != ShopLocationChangeRequest.STATUS_PENDING:
            return Response({"detail": "This location request has already been reviewed.", "status": item.status}, status=409)
        if shop.latitude != item.previous_latitude or shop.longitude != item.previous_longitude:
            return Response({"detail": "Approved location changed after this request was submitted; submit a new request."}, status=409)

        now = timezone.now()
        item.status = (
            ShopLocationChangeRequest.STATUS_APPROVED
            if decision == "approve" else ShopLocationChangeRequest.STATUS_REJECTED
        )
        item.reviewed_by = request.user
        item.review_reason = reason
        item.reviewed_at = now
        item.save(update_fields=["status", "reviewed_by", "review_reason", "reviewed_at"])
        if decision == "approve":
            shop.latitude = item.latitude
            shop.longitude = item.longitude
            shop.save(update_fields=["latitude", "longitude", "updated_at"])
        _audit(
            request.user,
            f"shop_location_request_{item.status}",
            "shop_location_change_request",
            item.id,
            f"{item.status.title()} location request for {shop.shop_name}.",
            {
                "shop_id": shop.id,
                "previous_latitude": str(item.previous_latitude) if item.previous_latitude is not None else None,
                "previous_longitude": str(item.previous_longitude) if item.previous_longitude is not None else None,
                "latitude": str(item.latitude), "longitude": str(item.longitude),
                "reason": reason,
            },
        )
    return Response(_location_request_summary(item))


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_shop_application_detail(request, application_id):
    shop = get_object_or_404(
        Shop.objects.select_related("owner").prefetch_related(
            "application_documents"
        ),
        id=application_id,
    )
    result = _application_summary(shop, request)
    result["status_history"] = _history(
        "shop",
        shop.id,
        include_metadata=True,
    )
    result["license_details"] = {
        "license_number": shop.license_number,
        "license_status": shop.license_status,
    }
    return Response(result)


def _decide_shop_application(request, application_id, decision):
    allowed_fields = {"note"} if decision == "approve" else {"reason", "note"}
    data = _validate_payload(
        request,
        allowed_fields,
        required_fields=("reason",) if decision == "reject" else (),
    )
    note = data.get("note", "")
    if note and not isinstance(note, str):
        raise ValidationError({"note": "Must be a string."})
    note = _safe_text(note.strip()) if note else ""
    reason = ""
    if decision == "reject":
        reason = _safe_text(_required_text(data, "reason"))

    with transaction.atomic():
        shop = get_object_or_404(
            Shop.objects.select_for_update().select_related("owner"),
            id=application_id,
        )
        if shop.license_status != "pending":
            raise ValidationError(
                {
                    "detail": "Only pending shop applications can be decided.",
                    "current_status": shop.license_status,
                }
            )

        documents = list(
            shop.application_documents.filter(
                verification_status="pending"
            ).order_by("-created_at", "-id")
        )
        if decision == "approve" and not documents:
            raise ValidationError(
                {
                    "documents": (
                        "A pending license document is required before approval."
                    )
                }
            )

        old_status = shop.license_status
        if decision == "approve":
            if documents[0].expiry_date <= timezone.localdate():
                raise ValidationError(
                    {
                        "documents": (
                            "The latest license document is expired and "
                            "cannot be approved."
                        )
                    }
                )
            shop.license_status = "verified"
            shop.is_active = True
            shop.is_suspended_by_super_admin = False
            for document in documents:
                document.verification_status = "verified"
                document.verified_at = timezone.now()
                document.save(
                    update_fields=["verification_status", "verified_at"]
                )
            action = "shop_application_approved"
            summary = f"Approved {shop.shop_name}."
        else:
            shop.license_status = "rejected"
            shop.is_active = False
            shop.application_documents.filter(
                verification_status="pending"
            ).update(verification_status="rejected")
            action = "shop_application_rejected"
            summary = f"Rejected {shop.shop_name}."

        shop.save(
            update_fields=[
                "license_status",
                "is_active",
                "is_suspended_by_super_admin",
                "updated_at",
            ]
        )
        metadata = {
            "old_status": old_status,
            "new_status": shop.license_status,
        }
        if note:
            metadata["note"] = note
        if reason:
            metadata["reason"] = reason
        _audit(
            request.user,
            action,
            "shop",
            shop.id,
            summary,
            metadata,
        )

    return Response(
        {
            "id": shop.id,
            "application_status": shop.license_status,
            "license_status": shop.license_status,
            "is_active": shop.is_active,
        }
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_approve_shop_application(request, application_id):
    return _decide_shop_application(request, application_id, "approve")


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_reject_shop_application(request, application_id):
    return _decide_shop_application(request, application_id, "reject")


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_request_shop_application_changes(request, application_id):
    data = _validate_payload(
        request,
        {"reason", "note"},
        required_fields=("reason",),
    )
    reason = _safe_text(_required_text(data, "reason"))
    note = data.get("note", "")
    if not isinstance(note, str):
        raise ValidationError({"note": "Must be a string."})
    note = _safe_text(note.strip())
    with transaction.atomic():
        shop = get_object_or_404(
            Shop.objects.select_for_update(),
            id=application_id,
        )
        if shop.license_status != "pending":
            raise ValidationError(
                {
                    "detail": "Changes can only be requested for pending applications.",
                    "current_status": shop.license_status,
                }
            )
        old_status = shop.license_status
        shop.license_status = "rejected"
        shop.is_active = False
        shop.save(
            update_fields=[
                "license_status",
                "is_active",
                "updated_at",
            ]
        )
        shop.application_documents.filter(
            verification_status="pending"
        ).update(verification_status="rejected")
        _audit(
            request.user,
            "shop_application_changes_requested",
            "shop",
            shop.id,
            f"Requested changes for {shop.shop_name}.",
            {
                "old_status": old_status,
                "new_status": shop.license_status,
                "reason": reason,
                "note": note,
            },
        )
    return Response({"id": shop.id, "application_status": shop.license_status})


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_shops(request):
    queryset = Shop.objects.select_related("owner").order_by("-created_at", "-id")
    shop_status = request.query_params.get("status")
    if shop_status:
        if shop_status == "active":
            queryset = queryset.filter(is_active=True)
        elif shop_status in ("inactive", "suspended"):
            if shop_status == "suspended":
                queryset = queryset.filter(is_suspended_by_super_admin=True)
            else:
                queryset = queryset.filter(is_active=False)
        elif shop_status in ("pending", "verified", "rejected", "expired"):
            queryset = queryset.filter(license_status=shop_status)
        elif shop_status != "all":
            raise ValidationError({"status": "Unsupported shop status."})

    license_status = request.query_params.get("license_status")
    if license_status:
        valid_statuses = {choice[0] for choice in Shop.LICENSE_STATUS_CHOICES}
        if license_status not in valid_statuses:
            raise ValidationError({"license_status": "Unsupported license status."})
        queryset = queryset.filter(license_status=license_status)

    search = request.query_params.get("search", "").strip()
    if search:
        queryset = queryset.filter(models_q_shop(search))
    return _paginated_response(request, queryset, _shop_summary)


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_shop_detail(request, shop_id):
    shop = get_object_or_404(
        Shop.objects.select_related("owner"),
        id=shop_id,
    )
    data = _shop_summary(shop)
    data.update(
        {
            "license_number": shop.license_number,
            "is_suspended_by_super_admin": shop.is_suspended_by_super_admin,
            "product_count": Product.objects.filter(shop=shop).count(),
            "order_count": shop.orders.count(),
            "status_history": _history("shop", shop.id),
        }
    )
    return Response(data)


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_shop_status(request, shop_id):
    data = _validate_payload(
        request,
        {"is_active", "reason"},
        required_fields=("is_active", "reason"),
    )
    if not isinstance(data["is_active"], bool):
        raise ValidationError({"is_active": "Must be a boolean."})
    reason = _safe_text(_required_text(data, "reason"))

    with transaction.atomic():
        shop = get_object_or_404(
            Shop.objects.select_for_update().select_related("owner"),
            id=shop_id,
        )
        if data["is_active"] and shop.license_status != "verified":
            raise ValidationError(
                {"is_active": "A shop must have a verified license before activation."}
            )
        old_active = shop.is_active
        old_suspended = shop.is_suspended_by_super_admin
        shop.is_active = data["is_active"]
        shop.is_suspended_by_super_admin = not data["is_active"]
        shop.save(
            update_fields=[
                "is_active",
                "is_suspended_by_super_admin",
                "updated_at",
            ]
        )
        action = "shop_activated" if shop.is_active else "shop_suspended"
        verb = "Activated" if shop.is_active else "Suspended"
        _audit(
            request.user,
            action,
            "shop",
            shop.id,
            f"{verb} {shop.shop_name}.",
            {
                "old_is_active": old_active,
                "new_is_active": shop.is_active,
                "old_is_suspended_by_super_admin": old_suspended,
                "new_is_suspended_by_super_admin": shop.is_suspended_by_super_admin,
                "reason": reason,
            },
        )
    return Response({"id": shop.id, "is_active": shop.is_active})


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_review_shop_license_document(request, shop_id, document_id):
    data = _validate_payload(
        request,
        {"decision", "reason"},
        required_fields=("decision",),
    )
    decision = data["decision"]
    if decision not in {"verify", "reject"}:
        raise ValidationError({"decision": "Choose verify or reject."})
    reason = data.get("reason", "")
    if not isinstance(reason, str):
        raise ValidationError({"reason": "Must be a string."})
    if decision == "reject" and not reason.strip():
        raise ValidationError({"reason": "A reason is required when rejecting."})
    reason = _safe_text(reason.strip()) if reason else ""

    with transaction.atomic():
        shop = get_object_or_404(
            Shop.objects.select_for_update(),
            id=shop_id,
        )
        if shop.license_status not in {"verified", "expired"}:
            raise ValidationError(
                {
                    "detail": (
                        "Initial applications must be reviewed through the "
                        "shop application approval endpoint."
                    )
                }
            )
        document = get_object_or_404(
            ShopLicenseDocument.objects.select_for_update(),
            id=document_id,
            shop=shop,
            verification_status="pending",
        )

        if decision == "verify":
            if document.expiry_date <= timezone.localdate():
                raise ValidationError(
                    {"document": "An expired license document cannot be verified."}
                )
            document.verification_status = "verified"
            document.verified_at = timezone.now()
            document.save(update_fields=["verification_status", "verified_at"])
            shop_update_fields = []
            if shop.license_number != document.license_number:
                shop.license_number = document.license_number
                shop_update_fields.append("license_number")
            if shop.license_status == "expired":
                shop.license_status = "verified"
                shop_update_fields.append("license_status")
            if shop_update_fields:
                shop.save(update_fields=[*shop_update_fields, "updated_at"])
            action = "shop_license_document_verified"
            summary = f"Verified a license document for {shop.shop_name}."
        else:
            document.verification_status = "rejected"
            document.save(update_fields=["verification_status"])
            action = "shop_license_document_rejected"
            summary = f"Rejected a license document for {shop.shop_name}."

        metadata = {
            "decision": decision,
            "document_id": document.id,
        }
        if reason:
            metadata["reason"] = reason
        _audit(
            request.user,
            action,
            "shop_license_document",
            document.id,
            summary,
            metadata,
        )

    return Response(
        {
            "shop_id": shop.id,
            "document_id": document.id,
            "verification_status": document.verification_status,
            "license_status": shop.license_status,
            "is_active": shop.is_active,
        }
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_customers(request):
    return _paginated_response(request, _customer_query(request), _customer_summary)


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_customer_detail(request, customer_id):
    user = get_object_or_404(User, id=customer_id, user_type="customer")
    return Response(_customer_summary(user))


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_customer_status(request, customer_id):
    data = _validate_payload(
        request,
        {"is_blocked", "reason"},
        required_fields=("is_blocked", "reason"),
    )
    if not isinstance(data["is_blocked"], bool):
        raise ValidationError({"is_blocked": "Must be a boolean."})
    reason = _safe_text(_required_text(data, "reason"))

    with transaction.atomic():
        user = get_object_or_404(
            User.objects.select_for_update(),
            id=customer_id,
            user_type="customer",
        )
        old_blocked = user.is_blocked
        user.is_blocked = data["is_blocked"]
        user.save(update_fields=["is_blocked"])
        action = "customer_blocked" if user.is_blocked else "customer_unblocked"
        verb = "Blocked" if user.is_blocked else "Unblocked"
        _audit(
            request.user,
            action,
            "customer",
            user.id,
            f"{verb} customer account {user.id}.",
            {
                "old_is_blocked": old_blocked,
                "new_is_blocked": user.is_blocked,
                "reason": reason,
            },
        )
    return Response(_customer_summary(user))


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_high_value_orders(request):
    return _paginated_response(
        request,
        _high_value_orders(request),
        _order_summary,
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_pending_orders(request):
    pending = (
        Order.objects.filter(
            status__in=HIGH_VALUE_APPROVAL_PENDING_STATUSES,
            total_amount__gte=get_high_value_threshold(),
            super_admin_approved_at__isnull=True,
        )
        .select_related("customer", "shop")
        .order_by("created_at")
    )
    results = []
    for order in pending:
        profile = getattr(order.customer, "customer_profile", None)
        results.append(
            {
                "id": order.id,
                "order_number": f"DRQ-{order.id:04d}",
                "status": order.status,
                "shop_id": order.shop_id,
                "shop_name": order.shop.shop_name,
                "customer_name": (profile.full_name if profile else "") or order.customer.get_full_name() or "Customer",
                "subtotal": str(order.subtotal),
                "application_fee": str(order.application_fee),
                "platform_fee": str(order.application_fee),
                "total_amount": str(order.total_amount),
                "pickup_mode": order.pickup_mode,
                "created_at": order.created_at,
            }
        )
    return Response({"count": len(results), "results": results})


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_restricted_shop_orders(request):
    queryset = (
        Order.objects.filter(
            shop__in=shops_requiring_super_admin_review(),
            status__in=REVIEWABLE_ORDER_STATUSES,
        )
        .select_related("shop", "customer")
        .order_by("-created_at", "-id")
    )

    def serialize(order):
        result = _order_summary(order)
        result["shop_restriction"] = shop_operation_block_reason(order.shop)
        result["review_status"] = (
            "authorized"
            if order_has_current_super_admin_review(order)
            else "pending"
        )
        return result

    return _paginated_response(request, queryset, serialize)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_review_restricted_shop_order(request, order_id):
    data = _validate_payload(
        request,
        {"decision", "note"},
        required_fields=("decision",),
    )
    if data["decision"] != "allow_continue":
        raise ValidationError(
            {"decision": "Choose allow_continue for an existing order."}
        )
    note = data.get("note", "")
    if not isinstance(note, str):
        raise ValidationError({"note": "Must be a string."})
    note = _safe_text(note.strip()) if note else ""

    with transaction.atomic():
        order = lock_order_with_shop(order_id)
        restriction = shop_operation_block_reason(order.shop)
        if order.status not in REVIEWABLE_ORDER_STATUSES:
            raise ValidationError(
                {"detail": "Only existing active orders can be reviewed."}
            )
        if restriction not in SHOP_REVIEWABLE_REASONS:
            raise ValidationError(
                {
                    "detail": (
                        "This order is not awaiting a reviewable shop "
                        "suspension or license-expiry decision."
                    ),
                    "shop_restriction": restriction,
                }
            )
        if order_has_current_super_admin_review(order):
            raise ValidationError(
                {"detail": "This order is already authorized to continue."}
            )

        metadata = {
            "decision": "allow_continue",
            "shop_id": order.shop_id,
            "shop_restriction": restriction,
            "shop_state": shop_review_snapshot(order.shop),
        }
        if note:
            metadata["note"] = note
        _audit(
            request.user,
            SHOP_REVIEW_APPROVAL_ACTION,
            "order",
            order.id,
            f"Authorized order #{order.id} to continue under shop review.",
            metadata,
        )

    return Response(
        {
            "order_id": order.id,
            "status": order.status,
            "review_status": "authorized",
        }
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_high_value_order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("shop", "customer").prefetch_related("items"),
        id=order_id,
        total_amount__gte=get_high_value_threshold(),
    )
    result = _order_summary(order)
    result["items"] = [
        {
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit_price": str(item.unit_price),
            "subtotal": str(item.subtotal),
        }
        for item in order.items.all()
    ]
    result["approval_history"] = _history("order", order.id)
    result["decision_state"] = (
        "pending"
        if (
            order.status in HIGH_VALUE_APPROVAL_PENDING_STATUSES
            and order.super_admin_approved_at is None
        )
        else "approved"
        if order.super_admin_approved_at is not None
        else "rejected"
        if order.status == "cancelled"
        and any(
            item["action"] == "high_value_order_rejected"
            for item in result["approval_history"]
        )
        else "not_pending"
    )
    return Response(result)


def _decide_high_value_order(request, order_id, decision):
    allowed_fields = {"note", "decision"}
    if decision == "reject":
        allowed_fields.add("reason")
    data = _validate_payload(
        request,
        allowed_fields,
        required_fields=("reason",) if decision == "reject" else (),
    )
    expected_request_decision = "approved" if decision == "approve" else "rejected"
    if "decision" in data and data["decision"] != expected_request_decision:
        raise ValidationError({"decision": f"Choose {expected_request_decision}."})
    note = data.get("note", "")
    if note and not isinstance(note, str):
        raise ValidationError({"note": "Must be a string."})
    note = _safe_text(note.strip()) if note else ""
    reason = ""
    if decision == "reject":
        reason = _safe_text(_required_text(data, "reason"))

    with transaction.atomic():
        order = lock_order_with_shop(
            order_id, total_amount__gte=get_high_value_threshold(),
        )
        if (
            order.status not in HIGH_VALUE_APPROVAL_PENDING_STATUSES
            or order.super_admin_approved_at is not None
        ):
            raise ValidationError(
                {
                    "detail": (
                        "Only active, unapproved high-value orders can be "
                        "decided."
                    ),
                    "current_status": order.status,
                }
            )

        old_status = order.status
        if decision == "approve":
            if order.status in {"pending_super_admin", "pending"}:
                order.status = "admin_approved"
            order.super_admin_approved_at = timezone.now()
            order.super_admin_approved_by = request.user
            action = "high_value_order_approved"
            verb = "approved"
            message = f"High-value order #{order.id} was approved by Super Admin."
        else:
            items = list(
                OrderItem.objects.filter(order=order)
                .select_related("product")
                .order_by("product_id")
            )
            for item in items:
                product = Product.objects.select_for_update().get(pk=item.product_id)
                product.stock_quantity += item.quantity
                product.save(update_fields=["stock_quantity", "updated_at"])
            order.status = "cancelled"
            action = "high_value_order_rejected"
            verb = "rejected"
            message = f"High-value order #{order.id} was rejected."

        update_fields = ["status", "updated_at"]
        if decision == "approve":
            update_fields.extend(
                ["super_admin_approved_at", "super_admin_approved_by"]
            )
        order.save(update_fields=update_fields)
        metadata = {"old_status": old_status, "new_status": order.status}
        if note:
            metadata["note"] = note
        if reason:
            metadata["reason"] = reason
        _audit(
            request.user,
            action,
            "order",
            order.id,
            f"Super Admin {verb} high-value order #{order.id}.",
            metadata,
        )

        Notification.objects.create(
            user=order.customer,
            order=order,
            notification_type="order_update",
            title="High-Value Order Update",
            message=message,
        )
        ShopAdminNotification.objects.create(
            shop_admin=order.shop.owner,
            notification_type="order_update",
            title="High-Value Order Update",
            message=message,
            order_id=order.id,
        )

    return Response(
        {
            "order_id": order.id,
            "status": order.status,
            "decision": expected_request_decision,
        }
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_order_approval(request, order_id):
    decision = request.data.get("decision")
    if decision not in ("approved", "rejected"):
        raise ValidationError({"decision": "Choose approved or rejected."})

    return _decide_high_value_order(
        request,
        order_id,
        "approve" if decision == "approved" else "reject",
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_approve_high_value_order(request, order_id):
    return _decide_high_value_order(request, order_id, "approve")


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_reject_high_value_order(request, order_id):
    return _decide_high_value_order(request, order_id, "reject")


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_reports(request):
    report_range = request.query_params.get("range", "7d")
    day_counts = {"7d": 7, "30d": 30, "90d": 90}
    if report_range not in day_counts:
        raise ValidationError({"range": "Allowed ranges are 7d, 30d, and 90d."})

    today = timezone.localdate(timezone=INDIA_TIME_ZONE)
    start_day = today - timedelta(days=day_counts[report_range] - 1)
    start, _ = _local_day_bounds(start_day)
    _, end = _local_day_bounds(today)
    orders = Order.objects.filter(created_at__gte=start, created_at__lt=end)
    daily_rows = {
        row["day"]: row
        for row in orders.annotate(
            day=TruncDate("created_at", tzinfo=INDIA_TIME_ZONE)
        ).values("day").annotate(
            count=Count("id"),
            gmv=Sum("subtotal"),
        )
    }
    order_trend = []
    if daily_rows:
        for offset in range(day_counts[report_range]):
            day = start_day + timedelta(days=offset)
            row = daily_rows.get(day, {})
            order_trend.append(
                {
                    "date": day.isoformat(),
                    "orders": row.get("count", 0),
                    "gmv": str(row.get("gmv") or Decimal("0.00")),
                }
            )

    top_shops = [
        {
            "shop_id": row["shop_id"],
            "shop_name": row["shop__shop_name"],
            "orders": row["orders"],
            "gmv": str(row["gmv"] or Decimal("0.00")),
        }
        for row in (
            orders.values("shop_id", "shop__shop_name")
            .annotate(orders=Count("id"), gmv=Sum("subtotal"))
            .order_by("-orders", "-gmv", "shop__shop_name")[:10]
        )
    ]

    return Response(
        {
            "range": report_range,
            "orders_count": orders.count(),
            "gmv": str(orders.aggregate(total=Sum("subtotal"))["total"] or Decimal("0.00")),
            "active_shops": Shop.objects.filter(
                is_active=True,
                license_status="verified",
            ).count(),
            "new_shops": Shop.objects.filter(created_at__gte=start, created_at__lt=end).count(),
            "new_customers": User.objects.filter(
                user_type="customer",
                date_joined__gte=start,
                date_joined__lt=end,
            ).count(),
            "order_trend": order_trend,
            "top_shops": top_shops,
            "category_trends": [],
        }
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_audit_logs(request):
    queryset = SuperAdminAuditLog.objects.select_related("actor")
    actor_id = request.query_params.get("actor_id")
    if actor_id:
        try:
            actor_id = int(actor_id)
        except ValueError:
            raise ValidationError({"actor_id": "Must be an integer."})
        queryset = queryset.filter(actor_id=actor_id)

    for field in ("action", "resource_type"):
        value = request.query_params.get(field)
        if value:
            queryset = queryset.filter(**{field: value})

    from_date = _date_filter(request.query_params.get("from"), "from")
    to_date = _date_filter(request.query_params.get("to"), "to", is_end=True)
    if from_date and to_date and from_date > to_date:
        raise ValidationError({"from": "Must not be later than to."})
    if from_date:
        queryset = queryset.filter(created_at__gte=from_date)
    if to_date:
        queryset = queryset.filter(created_at__lte=to_date)

    return _paginated_response(
        request,
        queryset,
        lambda item: {
            "id": item.id,
            "actor_id": item.actor_id,
            "actor_email": item.actor_email,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "summary": item.summary,
            "created_at": item.created_at,
        },
    )


@api_view(["GET", "POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_verification_sessions(request):
    if request.method == "GET":
        queryset = CustomerVerificationSession.objects.select_related(
            "user",
            "reviewed_by",
        ).order_by("-created_at", "-id")
        session_status = request.query_params.get("status")
        if session_status:
            valid_statuses = {
                choice[0] for choice in CustomerVerificationSession.STATUS_CHOICES
            }
            if session_status not in valid_statuses:
                raise ValidationError({"status": "Unsupported verification status."})
            queryset = queryset.filter(status=session_status)
        return _paginated_response(
            request,
            queryset,
            lambda item: _verification_session_summary(item, request),
        )

    data = _validate_payload(
        request,
        {
            "session_id",
            "status",
            "reviewer_note",
            "confirmed_date_of_birth",
        },
        required_fields=("session_id", "status"),
    )
    try:
        session_id = int(data["session_id"])
    except (TypeError, ValueError):
        raise ValidationError({"session_id": "Must be an integer."})
    decision = data["status"]
    if decision not in {"approved", "rejected"}:
        raise ValidationError({"status": "Choose approved or rejected."})
    reviewer_note = data.get("reviewer_note", "")
    if not isinstance(reviewer_note, str):
        raise ValidationError({"reviewer_note": "Must be a string."})
    if decision == "rejected" and not reviewer_note.strip():
        raise ValidationError({"reviewer_note": "Required when rejecting a document."})

    confirmed_date_of_birth = data.get("confirmed_date_of_birth")
    if confirmed_date_of_birth is not None:
        if not isinstance(confirmed_date_of_birth, str):
            raise ValidationError(
                {"confirmed_date_of_birth": "Use YYYY-MM-DD format."}
            )
        try:
            confirmed_date_of_birth = date.fromisoformat(
                confirmed_date_of_birth
            )
        except ValueError:
            raise ValidationError(
                {"confirmed_date_of_birth": "Use YYYY-MM-DD format."}
            ) from None
        if confirmed_date_of_birth.isoformat() != data["confirmed_date_of_birth"]:
            raise ValidationError(
                {"confirmed_date_of_birth": "Use YYYY-MM-DD format."}
            )
        if confirmed_date_of_birth > timezone.localdate():
            raise ValidationError(
                {
                    "confirmed_date_of_birth": (
                        "Date of birth cannot be in the future."
                    )
                }
            )

    with transaction.atomic():
        session = get_object_or_404(
            CustomerVerificationSession.objects.select_for_update().select_related(
                "user"
            ),
            pk=session_id,
        )
        if session.status != "pending":
            raise ValidationError({
                "detail": "Only pending verification sessions can be reviewed.",
                "current_status": session.status,
            })
        is_age_proof = session.document_type == AGE_PROOF_DOCUMENT_TYPE
        if decision == "approved" and is_age_proof:
            if confirmed_date_of_birth is None:
                raise ValidationError(
                    {
                        "confirmed_date_of_birth": (
                            "Required when approving age-proof documents."
                        )
                    }
                )
            if session.user.user_type != "customer":
                raise ValidationError(
                    {"detail": "Age proof can only be approved for a customer."}
                )
        elif confirmed_date_of_birth is not None:
            raise ValidationError(
                {
                    "confirmed_date_of_birth": (
                        "Only provide a confirmed date of birth when "
                        "approving an age-proof document."
                    )
                }
            )

        session.status = decision
        session.reviewer_note = _safe_text(reviewer_note.strip()) if reviewer_note else ""
        session.reviewed_by = request.user
        session.reviewed_at = timezone.now()
        session.save(
            update_fields=[
                "status",
                "reviewer_note",
                "reviewed_by",
                "reviewed_at",
            ]
        )
        age_verified = None
        if decision == "approved" and is_age_proof:
            user = User.objects.select_for_update().get(pk=session.user_id)
            user.date_of_birth = confirmed_date_of_birth
            age_verified = is_at_least_minimum_age(confirmed_date_of_birth)
            user.age_verified = age_verified
            user.age_verified_at = timezone.now() if age_verified else None
            user.save(
                update_fields=[
                    "date_of_birth",
                    "age_verified",
                    "age_verified_at",
                ]
            )
        _audit(
            request.user,
            f"customer_document_{decision}",
            "verification_session",
            session.id,
            f"{decision.title()} customer document verification {session.id}.",
            metadata=(
                {
                    "age_proof": True,
                    "confirmed_date_of_birth": (
                        confirmed_date_of_birth.isoformat()
                    ),
                    "age_verified": age_verified,
                }
                if decision == "approved" and is_age_proof
                else {"age_proof": is_age_proof}
            ),
        )
    response_data = CustomerVerificationSessionSerializer(session).data
    if decision == "approved" and is_age_proof:
        response_data["confirmed_date_of_birth"] = (
            confirmed_date_of_birth.isoformat()
        )
        response_data["age_verified"] = age_verified
    return Response(response_data)


def _verification_session_summary(session, request):
    return {
        **CustomerVerificationSessionSerializer(session).data,
        "user_id": session.user_id,
        "user_email": session.user.email,
        "reviewed_by": session.reviewed_by_id,
        "document_download_url": request.build_absolute_uri(
            f"/api/super-admin/verification-sessions/{session.id}/document/"
        ),
    }


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_verification_document(request, session_id):
    session = get_object_or_404(CustomerVerificationSession, pk=session_id)
    try:
        file_handle = session.document.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Verification document file is unavailable.") from exc
    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=(
            f"customer-verification-{session.id}"
            f"{PurePath(session.document.name).suffix}"
        ),
        content_type="application/octet-stream",
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_support_tickets(request):
    queryset = SupportTicket.objects.select_related("user").order_by(
        "-created_at",
        "-id",
    )
    ticket_status = request.query_params.get("status")
    if ticket_status:
        valid_statuses = {choice[0] for choice in SupportTicket.STATUS_CHOICES}
        if ticket_status not in valid_statuses:
            raise ValidationError({"status": "Unsupported support ticket status."})
        queryset = queryset.filter(status=ticket_status)
    return _paginated_response(
        request,
        queryset,
        lambda ticket: {
            **SupportTicketSerializer(ticket).data,
            "user_id": ticket.user_id,
            "user_email": ticket.user.email,
        },
    )


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_support_ticket_detail(request, ticket_id):
    data = _validate_payload(
        request,
        {"status", "admin_response"},
        required_fields=("status",),
    )
    ticket_status = data["status"]
    valid_statuses = {choice[0] for choice in SupportTicket.STATUS_CHOICES}
    if ticket_status not in valid_statuses:
        raise ValidationError({"status": "Unsupported support ticket status."})
    response_text = data.get("admin_response")
    if response_text is not None and not isinstance(response_text, str):
        raise ValidationError({"admin_response": "Must be a string."})
    with transaction.atomic():
        ticket = get_object_or_404(
            SupportTicket.objects.select_for_update(),
            pk=ticket_id,
        )
        old_status = ticket.status
        ticket.status = ticket_status
        if response_text is not None:
            ticket.admin_response = response_text.strip()
        ticket.save(update_fields=["status", "admin_response", "updated_at"])
        _audit(
            request.user,
            "support_ticket_updated",
            "support_ticket",
            ticket.id,
            f"Updated support ticket {ticket.id} status.",
            {"old_status": old_status, "new_status": ticket_status},
        )
    return Response(SupportTicketSerializer(ticket).data)


@api_view(["GET", "POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_legal_policies(request):
    if request.method == "GET":
        return Response(LegalPolicySerializer(
            LegalPolicy.objects.all(),
            many=True,
        ).data)
    data = _validate_payload(
        request,
        {"slug", "title", "version", "content", "is_published"},
        required_fields=("slug", "title", "version", "content"),
    )
    serializer = LegalPolicyAdminSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    policy = serializer.save(updated_by=request.user)
    _audit(
        request.user,
        "legal_policy_created",
        "legal_policy",
        policy.id,
        f"Created legal policy {policy.slug}.",
    )
    return Response(serializer.data, status=201)


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_legal_policy_detail(request, policy_id):
    data = _validate_payload(
        request,
        {"title", "version", "content", "is_published"},
    )
    policy = get_object_or_404(LegalPolicy, pk=policy_id)
    serializer = LegalPolicyAdminSerializer(policy, data=data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save(updated_by=request.user)
    _audit(
        request.user,
        "legal_policy_updated",
        "legal_policy",
        policy.id,
        f"Updated legal policy {policy.slug}.",
    )
    return Response(serializer.data)


@api_view(["GET", "PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def super_admin_platform_settings(request):
    settings, _ = PlatformSettings.objects.get_or_create(pk=1)
    if request.method == "GET":
        return Response({
            "high_value_order_threshold": str(HIGH_VALUE_THRESHOLD),
            "nearby_shop_radius_km": str(settings.nearby_shop_radius_km),
            "updated_at": settings.updated_at,
        })
    data = _validate_payload(
        request,
        {"high_value_order_threshold", "nearby_shop_radius_km"},
    )
    if not data:
        raise ValidationError({
            "detail": "Provide at least one platform setting to update."
        })
    if "high_value_order_threshold" in data:
        raise ValidationError({
            "high_value_order_threshold": [
                "The high-value order threshold is fixed at 15000.00 and "
                "cannot be changed."
            ]
        })

    updates = {}
    if "nearby_shop_radius_km" in data:
        try:
            radius = Decimal(str(data["nearby_shop_radius_km"]))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({
                "nearby_shop_radius_km": "Must be a valid decimal number."
            })
        if (
            not radius.is_finite()
            or not Decimal("0.10") <= radius <= Decimal("100.00")
        ):
            raise ValidationError({
                "nearby_shop_radius_km": "Must be between 0.10 and 100.00 km."
            })
        if radius != radius.quantize(Decimal("0.01")):
            raise ValidationError({
                "nearby_shop_radius_km": "Must have no more than 2 decimal places."
            })
        updates["nearby_shop_radius_km"] = radius

    with transaction.atomic():
        settings = PlatformSettings.objects.select_for_update().get(pk=1)
        old_values = {
            field: str(getattr(settings, field))
            for field in updates
        }
        for field, value in updates.items():
            setattr(settings, field, value)
        settings.updated_by = request.user
        settings.save(update_fields=[*updates, "updated_by", "updated_at"])
        _audit(
            request.user,
            "platform_settings_updated",
            "platform_settings",
            settings.id,
            "Updated platform settings.",
            {
                f"old_{field}": old_values[field]
                for field in updates
            } | {
                f"new_{field}": str(getattr(settings, field))
                for field in updates
            },
        )
    return Response({
        "high_value_order_threshold": str(HIGH_VALUE_THRESHOLD),
        "nearby_shop_radius_km": str(settings.nearby_shop_radius_km),
        "updated_at": settings.updated_at,
    })
