"""Shop-admin APIs for submitting, never directly applying, location changes."""

from collections.abc import Mapping
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import JWTAuthentication
from shops.fields import FiniteCoordinateDecimalField
from shops.models import Shop, ShopLocationChangeRequest


def _owned_shop(user):
    if getattr(user, "user_type", None) != "shop_admin":
        return None
    return Shop.objects.filter(owner=user).first()


def _access_error(user):
    if getattr(user, "user_type", None) != "shop_admin":
        return Response({"detail": "Shop Admin access required."}, status=403)
    return Response({"detail": "No shop is assigned to this Shop Admin."}, status=404)


def _request_data(item):
    return {
        "id": item.id,
        "status": item.status,
        "latitude": str(item.latitude),
        "longitude": str(item.longitude),
        "previous_latitude": str(item.previous_latitude) if item.previous_latitude is not None else None,
        "previous_longitude": str(item.previous_longitude) if item.previous_longitude is not None else None,
        "reason": item.reason,
        "review_reason": item.review_reason or None,
        "created_at": item.created_at,
        "reviewed_at": item.reviewed_at,
    }


def _payload(request):
    if not isinstance(request.data, Mapping):
        raise ValidationError({"detail": "A JSON object is required."})
    allowed = {"latitude", "longitude", "reason"}
    supplied = set(request.data.keys())
    unknown = supplied - allowed
    if unknown:
        raise ValidationError({"detail": "Unknown fields.", "fields": sorted(unknown)})
    missing = {"latitude", "longitude"} - supplied
    if missing:
        raise ValidationError({"detail": "Required fields are missing.", "fields": sorted(missing)})
    reason = request.data.get("reason", "")
    if not isinstance(reason, str):
        raise ValidationError({"reason": "Must be a string."})
    reason = reason.strip()
    if len(reason) > 500:
        raise ValidationError({"reason": "Ensure this field has no more than 500 characters."})
    latitude = FiniteCoordinateDecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    longitude = FiniteCoordinateDecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)
    return latitude.run_validation(request.data["latitude"]), longitude.run_validation(request.data["longitude"]), reason


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def shop_admin_location(request):
    """Available to an owned shop even while inactive, so first location can be requested."""
    shop = _owned_shop(request.user)
    if shop is None:
        return _access_error(request.user)
    pending = shop.location_change_requests.filter(status="pending").first()
    return Response({
        "shop_id": shop.id,
        "latitude": str(shop.latitude) if shop.latitude is not None else None,
        "longitude": str(shop.longitude) if shop.longitude is not None else None,
        "has_location": shop.latitude is not None and shop.longitude is not None,
        "pending_request": _request_data(pending) if pending else None,
    })


@api_view(["GET", "POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def shop_admin_location_requests(request):
    shop = _owned_shop(request.user)
    if shop is None:
        return _access_error(request.user)
    if request.method == "GET":
        requests = shop.location_change_requests.select_related("reviewed_by").all()
        pending = next((item for item in requests if item.status == "pending"), None)
        return Response({
            "shop_id": shop.id,
            "pending_request": _request_data(pending) if pending else None,
            "results": [_request_data(item) for item in requests],
        })

    latitude, longitude, reason = _payload(request)
    try:
        with transaction.atomic():
            # Lock order is always authoritative shop, then its requests.
            shop = get_object_or_404(Shop.objects.select_for_update(), pk=shop.pk, owner=request.user)
            if shop.latitude == latitude and shop.longitude == longitude:
                return Response(
                    {"detail": "Requested coordinates are already the approved location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if shop.location_change_requests.select_for_update().filter(status="pending").exists():
                return Response(
                    {"detail": "A location change request is already pending review."},
                    status=status.HTTP_409_CONFLICT,
                )
            item = ShopLocationChangeRequest.objects.create(
                shop=shop, requested_by=request.user, latitude=latitude, longitude=longitude,
                reason=reason, previous_latitude=shop.latitude, previous_longitude=shop.longitude,
            )
    except IntegrityError:
        return Response(
            {"detail": "A location change request is already pending review."},
            status=status.HTTP_409_CONFLICT,
        )
    return Response(_request_data(item), status=status.HTTP_201_CREATED)
