from rest_framework import serializers

from shops.fields import FiniteCoordinateDecimalField

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "product_name",
            "unit_price",
            "quantity",
            "subtotal",
        ]


class OrderVehicleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    vehicle_type = serializers.CharField()
    vehicle_number = serializers.CharField()
    vehicle_name = serializers.CharField()


class OrderSerializer(serializers.ModelSerializer):
    customer_id = serializers.IntegerField(read_only=True)
    platform_fee = serializers.DecimalField(
        source="application_fee", max_digits=10, decimal_places=2, read_only=True,
    )
    # Compatibility tombstone: old clients see no actionable OTP challenge.
    handover_challenge_id = serializers.SerializerMethodField()

    def get_handover_challenge_id(self, obj):
        return None
    order_number = serializers.SerializerMethodField()
    items = OrderItemSerializer(
        many=True,
        read_only=True,
    )
    vehicle_details = OrderVehicleSerializer(
        source="vehicle",
        read_only=True,
        allow_null=True,
    )

    @staticmethod
    def get_order_number(obj):
        return f"DRQ-{obj.pk:04d}"

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer_id",
            "handover_challenge_id",
            "shop",
            "status",
            "payment_method",
            "payment_status",
            "pickup_mode",
            "vehicle",
            "vehicle_details",
            "subtotal",
            "application_fee",
            "platform_fee",
            "total_amount",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class OrderLiveLocationUpdateSerializer(serializers.Serializer):
    latitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-90,
        max_value=90,
        required=False,
        help_text="Latitude from -90 to 90; required when is_sharing is true.",
    )
    longitude = FiniteCoordinateDecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        required=False,
        help_text="Longitude from -180 to 180; required when is_sharing is true.",
    )
    is_sharing = serializers.BooleanField(
        required=True,
        help_text="Set false to stop sharing; omit latitude and longitude.",
    )

    def validate(self, attrs):
        if attrs["is_sharing"] and (
            "latitude" not in attrs or "longitude" not in attrs
        ):
            raise serializers.ValidationError(
                "latitude and longitude are required while sharing."
            )
        if not attrs["is_sharing"] and (
            "latitude" in attrs or "longitude" in attrs
        ):
            raise serializers.ValidationError(
                "Do not include coordinates when stopping location sharing."
            )
        return attrs


class OrderLiveLocationResponseSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    is_sharing = serializers.BooleanField()
    latitude = serializers.FloatField(
        allow_null=True,
        help_text="Current latitude as a JSON number, or null when not sharing.",
    )
    longitude = serializers.FloatField(
        allow_null=True,
        help_text="Current longitude as a JSON number, or null when not sharing.",
    )
    updated_at = serializers.DateTimeField(
        allow_null=True,
        help_text="Last location-record update in UTC, or null if no record exists.",
    )


class OrderPaymentStatusSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    payment_method = serializers.CharField()
    payment_status = serializers.CharField()
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    application_fee = serializers.DecimalField(max_digits=10, decimal_places=2)
    platform_fee = serializers.DecimalField(
        source="application_fee", max_digits=10, decimal_places=2, read_only=True,
    )
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    ledger_status = serializers.CharField(allow_null=True)
    refund_status = serializers.CharField(allow_null=True)
    reconciliation_required = serializers.BooleanField()
    payment_eligible = serializers.BooleanField()
    next_action = serializers.CharField()


class OrderTrackingStepSerializer(serializers.Serializer):
    status = serializers.CharField()
    label = serializers.CharField()


class OrderTrackingSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    status = serializers.CharField()
    status_display = serializers.CharField()
    current_step = serializers.IntegerField()
    tracking_steps = OrderTrackingStepSerializer(many=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class StrictHandoverSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({
                "non_field_errors": ["A JSON object is required."]
            })
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({
                "non_field_errors": ["Unexpected request fields."]
            })
        return super().to_internal_value(data)


class HandoverCoordinatesSerializer(StrictHandoverSerializer):
    latitude = FiniteCoordinateDecimalField(
        max_digits=9, decimal_places=6, min_value=-90, max_value=90,
        help_text="Fresh foreground customer latitude for outside-shop handover.",
    )
    longitude = FiniteCoordinateDecimalField(
        max_digits=9, decimal_places=6, min_value=-180, max_value=180,
        help_text="Fresh foreground customer longitude for outside-shop handover.",
    )
