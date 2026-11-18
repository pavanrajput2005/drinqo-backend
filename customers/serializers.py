from rest_framework import serializers
from pathlib import PurePath
from django.utils import timezone

from shops.fields import FiniteCoordinateDecimalField
from shops.models import Shop
from shops.permissions import shop_can_accept_new_orders
from .verification_constants import (
    AGE_PROOF_CONSENT_POLICY_VERSION,
    AGE_PROOF_DOCUMENT_TYPE,
)

from .models import (
    AccountDeletionRequest,
    CustomerProfile,
    CustomerVerificationSession,
    CustomerVehicle,
    FavoriteShop,
    Cart,
    CartItem,
    LegalPolicy,
    Notification,
    SupportTicket,
    UserNotificationPreference,
    UserPushToken,
)


class CustomerProfileSerializer(serializers.ModelSerializer):
    phone_verified = serializers.SerializerMethodField()
    verified_phone_last4 = serializers.SerializerMethodField()
    class Meta:
        model = CustomerProfile
        fields = [
            "id",
            "full_name",
            "date_of_birth",
            "phone",
            "phone_verified",
            "verified_phone_last4",
            "email",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "profile_completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "phone_verified",
            "verified_phone_last4",
            "profile_completed_at",
            "created_at",
            "updated_at",
        ]

    def get_phone_verified(self, obj):
        from accounts.contact_identity import current_phone_verified
        return current_phone_verified(obj.user, obj.phone or obj.user.phone)

    def get_verified_phone_last4(self, obj):
        identity = getattr(obj.user, "verified_phone_identity", None)
        return identity.phone_last4 if identity else None

    def validate_date_of_birth(self, value):
        if value is not None and value > timezone.localdate():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value


class CustomerVehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerVehicle
        fields = [
            "id",
            "vehicle_type",
            "vehicle_number",
            "vehicle_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]


class FavoriteShopSerializer(serializers.ModelSerializer):
    shop_id = serializers.PrimaryKeyRelatedField(source="shop", queryset=Shop.objects.all())

    shop_name = serializers.CharField(
        source="shop.shop_name",
        read_only=True,
    )

    shop_city = serializers.CharField(
        source="shop.city",
        read_only=True,
    )

    class Meta:
        model = FavoriteShop
        fields = [
            "id",
            "shop_id",
            "shop_name",
            "shop_city",
            "created_at",
        ]

    def validate_shop_id(self, shop):
        if not shop_can_accept_new_orders(shop):
            raise serializers.ValidationError("Shop is not accepting new orders.")
        return shop


class CartItemSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(
        source="product.id",
        read_only=True,
    )

    product_name = serializers.CharField(
        source="product.name",
        read_only=True,
    )

    product_price = serializers.DecimalField(
        source="product.price",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )

    shop_id = serializers.IntegerField(
        source="product.shop.id",
        read_only=True,
    )

    shop_name = serializers.CharField(
        source="product.shop.shop_name",
        read_only=True,
    )

    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product_id",
            "product_name",
            "product_price",
            "shop_id",
            "shop_name",
            "quantity",
            "subtotal",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "product_id",
            "product_name",
            "product_price",
            "shop_id",
            "shop_name",
            "subtotal",
            "created_at",
            "updated_at",
        ]

    def get_subtotal(self, obj):
        return obj.product.price * obj.quantity


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(
        many=True,
        read_only=True,
    )

    total_amount = serializers.SerializerMethodField()

    total_items = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            "id",
            "items",
            "total_items",
            "total_amount",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "items",
            "total_items",
            "total_amount",
            "created_at",
            "updated_at",
        ]

    def get_total_items(self, obj):
        return sum(
            item.quantity
            for item in obj.items.all()
        )

    def get_total_amount(self, obj):
        return sum(
            item.product.price * item.quantity
            for item in obj.items.all()
        )


class StrictPositiveIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, (int, str)):
            self.fail("invalid")
        if isinstance(data, str):
            value = data.strip()
            digits = value[1:] if value[:1] in {"+", "-"} else value
            if not digits or not digits.isdigit():
                self.fail("invalid")
            data = value
        return super().to_internal_value(data)


class CartItemAddRequestSerializer(serializers.Serializer):
    product_id = StrictPositiveIntegerField(min_value=1)
    quantity = StrictPositiveIntegerField(min_value=1, default=1)


class CartItemQuantityRequestSerializer(serializers.Serializer):
    quantity = StrictPositiveIntegerField(min_value=1)


class CheckoutPreviewItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()

    product_name = serializers.CharField()

    quantity = serializers.IntegerField()

    unit_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    subtotal = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )


class CheckoutRequestSerializer(serializers.Serializer):
    pickup_mode = serializers.ChoiceField(
        choices=("walk_in", "vehicle"),
        default="vehicle",
        help_text="Staff handover outside the shop within 100 metres: on foot or at your vehicle.",
    )
    vehicle_id = serializers.IntegerField(
        min_value=1,
        required=False,
    )
    latitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-90,
        max_value=90,
    )
    longitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
    )

    def validate(self, attrs):
        if attrs["pickup_mode"] == "vehicle" and "vehicle_id" not in attrs:
            raise serializers.ValidationError(
                {"vehicle_id": "Vehicle ID is required for vehicle pickup."}
            )
        if attrs["pickup_mode"] == "walk_in" and "vehicle_id" in attrs:
            raise serializers.ValidationError(
                {"vehicle_id": "Do not include a vehicle for walk-in pickup."}
            )
        return attrs


class CheckoutPreviewSerializer(serializers.Serializer):
    can_checkout = serializers.BooleanField()
    detail = serializers.CharField(required=False)

    shop_id = serializers.IntegerField()

    shop_name = serializers.CharField()

    items = CheckoutPreviewItemSerializer(
        many=True,
    )

    total_items = serializers.IntegerField()

    subtotal = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    minimum_order_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    distance_to_shop_m = serializers.FloatField(
        allow_null=True,
    )
    pickup_radius_m = serializers.IntegerField()

    application_fee = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    platform_fee = serializers.DecimalField(
        source="application_fee",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )

    delivery_fee = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    total_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    pickup_mode = serializers.CharField()

    vehicle = serializers.DictField(
        allow_null=True,
    )


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "order",
            "notification_type",
            "title",
            "message",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields


class CustomerVerificationSessionSerializer(serializers.ModelSerializer):
    document = serializers.FileField(write_only=True)
    consent = serializers.BooleanField(write_only=True, required=False)

    class Meta:
        model = CustomerVerificationSession
        fields = [
            "id",
            "document",
            "document_type",
            "consent",
            "consent_at",
            "consent_policy_version",
            "status",
            "reviewer_note",
            "created_at",
            "reviewed_at",
        ]
        read_only_fields = [
            "id",
            "consent_at",
            "status",
            "reviewer_note",
            "created_at",
            "reviewed_at",
        ]

    def validate(self, attrs):
        consent = attrs.pop("consent", None)
        if attrs["document_type"] != AGE_PROOF_DOCUMENT_TYPE:
            return attrs
        if consent is not True:
            raise serializers.ValidationError(
                {"consent": "Explicit consent is required for age-proof processing."}
            )
        if attrs.get("consent_policy_version") != AGE_PROOF_CONSENT_POLICY_VERSION:
            raise serializers.ValidationError(
                {
                    "consent_policy_version": (
                        "Unsupported age-proof consent policy version."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        is_age_proof = validated_data["document_type"] == AGE_PROOF_DOCUMENT_TYPE
        if is_age_proof:
            validated_data["consent_at"] = timezone.now()
        return super().create(validated_data)

    def validate_document(self, value):
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("Document must be 10 MB or smaller.")
        if PurePath(value.name).suffix.lower() not in {
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
        }:
            raise serializers.ValidationError("Use a PDF, JPG, or PNG document.")
        content_type = (getattr(value, "content_type", "") or "").lower()
        if content_type and content_type not in {
            "application/pdf", "image/jpeg", "image/png",
        }:
            raise serializers.ValidationError("Document content type is not allowed.")
        header = value.read(16)
        value.seek(0)
        suffix = PurePath(value.name).suffix.lower()
        valid_signature = {
            ".pdf": header.startswith(b"%PDF-"),
            ".jpg": header.startswith(b"\xff\xd8\xff"),
            ".jpeg": header.startswith(b"\xff\xd8\xff"),
            ".png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        }[suffix]
        if not valid_signature:
            raise serializers.ValidationError("Document content does not match its file type.")
        return value


class SupportTicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "subject",
            "message",
            "status",
            "admin_response",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "admin_response",
            "created_at",
            "updated_at",
        ]


class AccountDeletionRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountDeletionRequest
        fields = [
            "id",
            "status",
            "requested_at",
            "scheduled_for",
            "cancelled_at",
            "completed_at",
        ]
        read_only_fields = fields


class UserNotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserNotificationPreference
        fields = [
            "push_enabled",
            "email_enabled",
            "order_updates_enabled",
            "promotions_enabled",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class UserPushTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=4096, trim_whitespace=True)
    platform = serializers.ChoiceField(choices=("android", "ios", "web"))


class LegalPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalPolicy
        fields = ["slug", "title", "version", "content", "updated_at"]
        read_only_fields = fields


class LegalPolicyAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalPolicy
        fields = ["id", "slug", "title", "version", "content", "is_published", "updated_at"]
        read_only_fields = ["id", "updated_at"]
