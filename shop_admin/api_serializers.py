from decimal import Decimal

from rest_framework import serializers

from accounts.models import User
from customers.models import Notification
from orders.constants import order_requires_high_value_approval
from orders.models import Order, OrderItem
from products.models import Product, StockMovement
from shops.models import Shop
from .models import ShopAdminNotification


class ShopAdminUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    role = serializers.CharField(source="user_type", read_only=True)

    class Meta:
        model = User
        fields = ["id", "firebase_uid", "full_name", "email", "role"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username or obj.email


class AssignedShopSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="shop_name", read_only=True)

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "phone",
            "is_open",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "opening_hours",
            "is_active",
            "license_status",
        ]
        read_only_fields = ["id", "is_active", "license_status"]


class ShopAdminMeSerializer(serializers.Serializer):
    user = ShopAdminUserSerializer()
    shop = AssignedShopSerializer(allow_null=True)


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
            "low_stock_threshold",
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
            raise serializers.ValidationError("Price must be greater than zero.")
        return value

    def validate_stock_quantity(self, value):
        if value < 0:
            raise serializers.ValidationError("Stock quantity cannot be negative.")
        return value

    def validate_low_stock_threshold(self, value):
        if value < 0:
            raise serializers.ValidationError("Low-stock threshold cannot be negative.")
        return value


class InventorySerializer(serializers.ModelSerializer):
    low_stock = serializers.SerializerMethodField()
    last_updated = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "category",
            "stock_quantity",
            "low_stock_threshold",
            "low_stock",
            "last_updated",
        ]

    def get_low_stock(self, obj):
        return obj.stock_quantity <= obj.low_stock_threshold


class StockAdjustmentSerializer(serializers.Serializer):
    quantity_change = serializers.IntegerField()
    reason = serializers.ChoiceField(choices=StockMovement.REASON_CHOICES)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_quantity_change(self, value):
        if value == 0:
            raise serializers.ValidationError("Quantity change cannot be zero.")
        return value


class StockMovementSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "quantity_change",
            "quantity_before",
            "quantity_after",
            "reason",
            "note",
            "changed_by_name",
            "created_at",
        ]

    def get_changed_by_name(self, obj):
        return obj.changed_by.get_full_name() or obj.changed_by.email


class OrderItemAdminSerializer(serializers.ModelSerializer):
    line_total = serializers.DecimalField(
        source="subtotal",
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = OrderItem
        fields = ["id", "product_name", "quantity", "unit_price", "line_total"]


class ShopAdminOrderSerializer(serializers.ModelSerializer):
    customer_id = serializers.IntegerField(read_only=True)
    order_number = serializers.SerializerMethodField()
    customer_name = serializers.SerializerMethodField()
    customer_phone = serializers.CharField(source="customer.phone", read_only=True)
    vehicle_registration = serializers.CharField(
        source="vehicle.vehicle_number",
        read_only=True,
        allow_null=True,
    )
    handoff_location = serializers.SerializerMethodField()
    platform_fee = serializers.DecimalField(
        source="application_fee", max_digits=10, decimal_places=2, read_only=True,
    )
    delivery_charge = serializers.SerializerMethodField()
    items = OrderItemAdminSerializer(many=True, read_only=True)
    can_decide = serializers.SerializerMethodField()
    customer_notifications = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "status",
            "customer_name",
            "customer_id",
            "customer_phone",
            "pickup_mode",
            "vehicle_registration",
            "handoff_location",
            "subtotal",
            "application_fee",
            "platform_fee",
            "delivery_charge",
            "total_amount",
            "payment_method",
            "payment_status",
            "created_at",
            "items",
            "can_decide",
            "reject_reason_code",
            "decided_at",
            "customer_notifications",
        ]

    def get_order_number(self, obj):
        return f"DRQ-{obj.id:04d}"

    def get_customer_name(self, obj):
        profile = getattr(obj.customer, "customer_profile", None)
        return (profile.full_name if profile else "") or obj.customer.get_full_name() or "Customer"

    def get_handoff_location(self, obj):
        return "Outside shop on foot" if obj.pickup_mode == "walk_in" else "Outside shop at selected vehicle"

    def get_delivery_charge(self, obj):
        return "0.00"

    def get_can_decide(self, obj):
        return (
            obj.status in {"pending", "admin_approved"}
            and not order_requires_high_value_approval(obj)
        )

    def get_customer_notifications(self, obj):
        return list(
            Notification.objects.filter(order=obj).values(
                "id",
                "title",
                "message",
                "notification_type",
                "is_read",
                "created_at",
            )
        )


class ShopAdminNotificationSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(source="order.id", read_only=True, allow_null=True)
    type = serializers.CharField(source="notification_type", read_only=True)

    class Meta:
        model = ShopAdminNotification
        fields = ["id", "title", "message", "type", "order_id", "is_read", "created_at"]
        read_only_fields = fields
