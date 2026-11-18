from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from drf_spectacular.utils import OpenApiResponse, extend_schema
from accounts.permissions import CustomerOnly
from .handover_views import HandoverBaseView
from .serializers import HandoverCoordinatesSerializer
from .staff_handover import confirm_staff_handover, report_customer_location


class ForegroundLocationSerializer(HandoverCoordinatesSerializer):
    location_captured_at = serializers.DateTimeField()


class StaffConfirmationSerializer(ForegroundLocationSerializer):
    customer_id = serializers.IntegerField(min_value=1)
    customer_checked = serializers.BooleanField()
    order_checked = serializers.BooleanField()
    physically_handed_over = serializers.BooleanField()
    outside_shop_confirmed = serializers.BooleanField()

    def validate(self, attrs):
        for key in ("customer_checked", "order_checked", "physically_handed_over", "outside_shop_confirmed"):
            if self.initial_data.get(key) is not True:
                raise serializers.ValidationError({key: "Explicit true acknowledgment is required."})
        return attrs


class StaffConfirmationThrottle(UserRateThrottle):
    scope = "staff_handover"
    rate = "30/min"


class StaffConfirmHandoverView(HandoverBaseView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StaffConfirmationThrottle]
    serializer_class = StaffConfirmationSerializer

    def post(self, request, order_id):
        payload = self.get_serializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(confirm_staff_handover(request.user, order_id, payload.validated_data))


class CustomerHandoverLocationView(HandoverBaseView):
    permission_classes = [CustomerOnly]
    throttle_classes = [StaffConfirmationThrottle]
    serializer_class = ForegroundLocationSerializer

    def post(self, request, pk):
        payload = self.get_serializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(report_customer_location(request.user, pk, payload.validated_data))


class RetiredHandoverOTPView(HandoverBaseView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(deprecated=True, responses={410: OpenApiResponse(description="Handover OTP retired.")})
    def get(self, request, **kwargs):
        return Response({"code": "handover_otp_retired", "detail": "Handover OTP is retired. The owning Shop Admin must explicitly confirm outside-shop handover."}, status=410)

    @extend_schema(request=None, deprecated=True, responses={410: OpenApiResponse(description="Handover OTP retired.")})
    def post(self, request, **kwargs):
        return self.get(request, **kwargs)
