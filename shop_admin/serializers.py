
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer

from products.models import Product
from .models import (
    ShopAdminProfile,
    Shop,
    ShopLicenseDocument,
    ShopAdminNotification,
)


@extend_schema_serializer(component_name="LegacyShop")
class ShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "address",
            "city",
            "state",
            "pincode",
            "latitude",
            "longitude",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ShopAdminProfileSerializer(serializers.ModelSerializer):
    shop = ShopSerializer(read_only=True)

    class Meta:
        model = ShopAdminProfile
        fields = [
            "id",
            "shop_name",
            "phone",
            "email",
            "is_active",
            "created_at",
            "shop",
        ]
        read_only_fields = ["id", "created_at"]


class ShopLicenseDocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ShopLicenseDocument
        fields = [
            "id",
            "shop",
            "license_number",
            "document",
            "expiry_date",
            "verification_status",
            "verified_at",
            "created_at",
            "download_url",
        ]
        read_only_fields = [
            "id",
            "verification_status",
            "verified_at",
            "created_at",
            "download_url",
        ]
        extra_kwargs = {"document": {"write_only": True}}

    def get_download_url(self, obj):
        request = self.context.get("request")
        path = f"/api/shop-admin/licenses/{obj.id}/download/"
        return request.build_absolute_uri(path) if request else path


class ShopAdminNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopAdminNotification
        fields = [
            "id",
            "notification_type",
            "title",
            "message",
            "order_id",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields


class ShopAdminProductSerializer(serializers.ModelSerializer):
    shop_name = serializers.CharField(
        source="shop.shop_name",
        read_only=True,
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "shop",
            "shop_name",
            "name",
            "brand",
            "category",
            "description",
            "price",
            "stock_quantity",
            "image",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "shop",
            "shop_name",
            "created_at",
            "updated_at",
        ]

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Product price must be greater than zero."
            )
        return value

    def validate_stock_quantity(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Stock quantity cannot be negative."
            )
        return value
