from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import FileResponse, Http404
from pathlib import PurePath
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import JWTAuthentication
from shops.permissions import IsApprovedActiveShopAdmin

from .models import ShopAdminProfile, ShopLicenseDocument, SuperAdminAuditLog
from .serializers import (
    ShopAdminProfileSerializer,
    ShopLicenseDocumentSerializer,
)
from shops.models import Shop as MainShop
from shops.models import ShopLicenseDocument as MainShopLicenseDocument
from shops.serializers import (
    ShopApplicationSubmissionSerializer,
    ShopLicenseDocumentUploadSerializer,
    ShopLicenseDocumentSerializer as ShopApplicationLicenseDocumentSerializer,
)

User = get_user_model()


class ShopAdminProfileView(generics.RetrieveUpdateAPIView):
    """Read/update the legacy Shop Admin profile for the current user."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsApprovedActiveShopAdmin]
    serializer_class = ShopAdminProfileSerializer

    def get_object(self):
        return get_object_or_404(ShopAdminProfile, user=self.request.user)


def _legacy_shop_for_user(user):
    """Return the legacy shop linked to this user's ShopAdminProfile."""
    profile = ShopAdminProfile.objects.filter(user=user).first()
    if profile is None:
        return None
    return getattr(profile, "shop", None)


class ShopLicenseDocumentListCreateView(generics.ListCreateAPIView):
    """List/create license documents for the current user's legacy shop."""
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = ShopLicenseDocumentSerializer

    def get_queryset(self):
        shop = _legacy_shop_for_user(self.request.user)
        if shop is None:
            return ShopLicenseDocument.objects.none()
        return ShopLicenseDocument.objects.filter(shop=shop).order_by("-created_at")

    def perform_create(self, serializer):
        shop = _legacy_shop_for_user(self.request.user)
        if shop is None:
            raise NotFound("No legacy shop is linked to this Shop Admin profile.")
        serializer.save(shop=shop)


class ShopLicenseDocumentDetailView(generics.RetrieveAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = ShopLicenseDocumentSerializer
    lookup_url_kwarg = "document_id"

    def get_queryset(self):
        shop = _legacy_shop_for_user(self.request.user)
        if shop is None:
            return ShopLicenseDocument.objects.none()
        return ShopLicenseDocument.objects.filter(shop=shop)


class ShopLicenseDocumentDownloadView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        shop = _legacy_shop_for_user(request.user)
        if shop is None:
            raise NotFound("No legacy shop is linked to this Shop Admin profile.")
        document = get_object_or_404(
            ShopLicenseDocument,
            pk=document_id,
            shop=shop,
        )
        try:
            file_handle = document.document.open("rb")
        except FileNotFoundError as exc:
            raise Http404("License document file is unavailable.") from exc
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=PurePath(document.document.name).name,
        )


class ShopApplicationDocumentDetailView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        if request.user.user_type != "shop_admin":
            raise PermissionDenied("Shop Admin access required.")
        document = get_object_or_404(
            MainShopLicenseDocument.objects.select_related("shop"),
            pk=document_id,
            shop__owner=request.user,
        )
        return Response(
            ShopApplicationLicenseDocumentSerializer(
                document,
                context={"request": request},
            ).data
        )


class ShopApplicationView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        self._require_shop_admin(request.user)
        shop = get_object_or_404(MainShop, owner=request.user)
        return self._serialize_application(request, shop)

    def post(self, request):
        return _submit_shop_application(request)

    @staticmethod
    def _require_shop_admin(user):
        if user.user_type != "shop_admin":
            raise PermissionDenied("Shop Admin access required.")

    @staticmethod
    def _serialize_application(request, shop, status_code=200):
        documents = ShopApplicationLicenseDocumentSerializer(
            shop.application_documents.all(),
            many=True,
            context={"request": request},
        ).data
        review_history = [
            {
                "action": item.action,
                "summary": item.summary,
                "reason": item.metadata.get("reason"),
                "note": item.metadata.get("note"),
                "created_at": item.created_at,
            }
            for item in SuperAdminAuditLog.objects.filter(
                resource_type="shop",
                resource_id=shop.id,
                action__in={
                    "shop_application_submitted",
                    "shop_application_resubmitted",
                    "shop_application_changes_requested",
                    "shop_application_rejected",
                    "shop_application_approved",
                },
            ).order_by("created_at", "id")
        ]
        return Response(
            {
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
                "application_status": shop.license_status,
                "is_active": shop.is_active,
                "latitude": str(shop.latitude) if shop.latitude is not None else None,
                "longitude": str(shop.longitude) if shop.longitude is not None else None,
                "documents": documents,
                "review_history": review_history,
                "submitted_at": shop.created_at,
            },
            status=status_code,
        )


class ShopLicenseDocumentUploadView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        if request.user.user_type != "shop_admin":
            raise PermissionDenied("Shop Admin access required.")

        serializer = ShopLicenseDocumentUploadSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            shop = get_object_or_404(
                MainShop.objects.select_for_update(),
                owner=request.user,
            )
            if shop.license_status not in {"verified", "expired"}:
                raise ValidationError(
                    {
                        "detail": (
                            "License renewals are available only for "
                            "approved shops."
                        )
                    }
                )

            # The shop row is locked before looking for a pending submission.
            # That serializes concurrent uploads for this shop and prevents a
            # second renewal from being created while the first awaits review.
            pending_document = (
                MainShopLicenseDocument.objects.select_for_update()
                .filter(shop=shop, verification_status="pending")
                .order_by("-created_at", "-id")
                .first()
            )
            if pending_document is not None:
                raise ValidationError(
                    {
                        "detail": (
                            "A license document is already pending review."
                        ),
                        "pending_document_id": pending_document.id,
                    }
                )

            values = serializer.validated_data
            document = MainShopLicenseDocument.objects.create(
                shop=shop,
                license_number=values["license_number"],
                document=values["license_document"],
                expiry_date=values["expiry_date"],
            )
            SuperAdminAuditLog.objects.create(
                actor=request.user,
                actor_email=request.user.email,
                action="shop_license_renewal_submitted",
                resource_type="shop",
                resource_id=shop.id,
                summary=f"Submitted a license renewal for {shop.shop_name}.",
                metadata={
                    "document_id": document.id,
                    "status": "pending",
                },
            )

        return Response(
            ShopApplicationLicenseDocumentSerializer(
                document,
                context={"request": request},
            ).data,
            status=201,
        )


class ShopOnboardingView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        return _submit_shop_application(request, allow_customer=True)


def _submit_shop_application(request, allow_customer=False):
    allowed_user_types = {"shop_admin"}
    if allow_customer:
        allowed_user_types.add("customer")
    if request.user.user_type not in allowed_user_types:
        raise PermissionDenied("Shop onboarding is not allowed for this account.")

    serializer = ShopApplicationSubmissionSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data.copy()
    document_file = values.pop("license_document")
    expiry_date = values.pop("expiry_date")
    license_number = values["license_number"]

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        if user.user_type not in allowed_user_types:
            raise PermissionDenied("Shop onboarding is not allowed for this account.")

        shop = MainShop.objects.select_for_update().filter(owner=user).first()
        old_status = shop.license_status if shop else None
        if shop is None:
            shop = MainShop.objects.create(
                owner=user,
                license_status="pending",
                is_active=False,
                **values,
            )
        elif shop.license_status in {"rejected", "expired"}:
            shop.application_documents.filter(
                verification_status="pending"
            ).update(verification_status="rejected")
            for field, value in values.items():
                setattr(shop, field, value)
            shop.license_status = "pending"
            shop.is_active = False
            shop.is_suspended_by_super_admin = False
            shop.save(
                update_fields=[
                    *values.keys(),
                    "license_status",
                    "is_active",
                    "is_suspended_by_super_admin",
                    "updated_at",
                ]
            )
        else:
            raise ValidationError(
                {
                    "detail": (
                        "An application already exists and is not rejected; "
                        "it cannot be submitted again."
                    ),
                    "current_status": shop.license_status,
                }
            )

        if user.user_type == "customer":
            user.user_type = "shop_admin"
            user.save(update_fields=["user_type"])

        MainShopLicenseDocument.objects.create(
            shop=shop,
            license_number=license_number,
            document=document_file,
            expiry_date=expiry_date,
        )
        SuperAdminAuditLog.objects.create(
            actor=user,
            actor_email=user.email,
            action=(
                "shop_application_resubmitted"
                if old_status == "rejected"
                else "shop_application_submitted"
            ),
            resource_type="shop",
            resource_id=shop.id,
            summary=f"Submitted shop application for {shop.shop_name}.",
            metadata={
                "old_status": old_status,
                "new_status": "pending",
            },
        )

    return ShopApplicationView._serialize_application(
        request,
        shop,
        status_code=201,
    )


class ShopApplicationDocumentDownloadView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(
            MainShopLicenseDocument.objects.select_related("shop"),
            pk=document_id,
        )
        if request.user.user_type != "super_admin" and not (
            request.user.user_type == "shop_admin"
            and document.shop.owner_id == request.user.id
        ):
            raise PermissionDenied("You cannot access this license document.")

        return FileResponse(
            document.document.open("rb"),
            as_attachment=True,
            filename=f"license-document-{document.id}{PurePath(document.document.name).suffix}",
            content_type="application/octet-stream",
        )
