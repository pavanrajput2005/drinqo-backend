from pathlib import PurePath

from django.utils import timezone
from rest_framework import serializers

from .fields import CoordinateQueryField, FiniteCoordinateDecimalField
from .models import Shop, ShopLicenseDocument


class NearbyShopCoordinatesSerializer(serializers.Serializer):
    latitude = CoordinateQueryField(minimum=-90, maximum=90)
    longitude = CoordinateQueryField(minimum=-180, maximum=180)


class ShopSerializer(serializers.ModelSerializer):
    latitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=-90,
        max_value=90,
    )
    longitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=-180,
        max_value=180,
    )

    class Meta:
        model = Shop
        fields = [
            "id",
            "shop_name",
            "phone",
            "email",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "latitude",
            "longitude",
            "license_number",
            "license_status",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "license_status",
            "is_active",
            "created_at",
            "updated_at",
        ]


class ShopAdminShopUpdateSerializer(ShopSerializer):
    is_open = serializers.BooleanField(required=False)
    opening_hours = serializers.JSONField(required=False)

    class Meta(ShopSerializer.Meta):
        fields = [
            "phone",
            "email",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "opening_hours",
            "is_open",
        ]
        read_only_fields = []


class CustomerShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            "id",
            "shop_name",
            "phone",
            "email",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "latitude",
            "longitude",
        ]
        read_only_fields = fields


class ShopApplicationSubmissionSerializer(serializers.Serializer):
    shop_name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=15)
    email = serializers.EmailField(required=False, allow_blank=True)
    address_line1 = serializers.CharField(max_length=255)
    address_line2 = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100)
    postal_code = serializers.CharField(max_length=10)
    latitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        min_value=-90,
        max_value=90,
    )
    longitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        min_value=-180,
        max_value=180,
    )
    license_number = serializers.CharField(max_length=100)
    license_document = serializers.FileField()
    expiry_date = serializers.DateField()

    def validate_license_number(self, value):
        owner = self.context["request"].user
        existing = Shop.objects.filter(license_number=value).exclude(owner=owner)
        if existing.exists():
            raise serializers.ValidationError("This license number is already in use.")
        return value

    def validate(self, attrs):
        has_latitude = "latitude" in self.initial_data
        has_longitude = "longitude" in self.initial_data
        if has_latitude != has_longitude:
            raise serializers.ValidationError(
                "latitude and longitude must be supplied together."
            )
        return attrs

    def validate_license_document(self, value):
        return validate_license_document_file(value)

    def validate_expiry_date(self, value):
        return validate_license_expiry_date(value)


class ShopLicenseDocumentUploadSerializer(serializers.Serializer):
    license_number = serializers.CharField(max_length=100)
    license_document = serializers.FileField()
    expiry_date = serializers.DateField()

    def validate_license_number(self, value):
        owner = self.context["request"].user
        existing = Shop.objects.filter(license_number=value).exclude(owner=owner)
        if existing.exists():
            raise serializers.ValidationError("This license number is already in use.")
        return value

    def validate_license_document(self, value):
        return validate_license_document_file(value)

    def validate_expiry_date(self, value):
        return validate_license_expiry_date(value)


def validate_license_document_file(value):
    if value.size > 10 * 1024 * 1024:
        raise serializers.ValidationError("License document must be 10 MB or smaller.")
    extension = PurePath(value.name).suffix.lower()
    if extension not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise serializers.ValidationError("Use a PDF, JPG, or PNG document.")
    return value


def validate_license_expiry_date(value):
    if value <= timezone.localdate():
        raise serializers.ValidationError("License document must not be expired.")
    return value


class ShopLicenseDocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ShopLicenseDocument
        fields = [
            "id",
            "license_number",
            "expiry_date",
            "verification_status",
            "verified_at",
            "created_at",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        request = self.context.get("request")
        path = f"/api/shop-admin/application/documents/{obj.id}/download/"
        return request.build_absolute_uri(path) if request else path
