"""Customer payment APIs and the separately authenticated provider webhook."""
from django.utils.cache import patch_vary_headers
from django.db import transaction
from django.views.decorators.debug import sensitive_variables
from rest_framework import generics, serializers
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from accounts.authentication import JWTAuthentication
from accounts.permissions import CustomerOnly
from .handover_privacy import protect_handover_request
from .payments import handle_webhook, initiate_payment, reconcile_payment, verify_customer_payment
from .razorpay_gateway import GatewayError
from .serializers import StrictHandoverSerializer


class PaymentRequestSerializer(StrictHandoverSerializer):
    pass


class PaymentVerifySerializer(StrictHandoverSerializer):
    razorpay_order_id = serializers.RegexField(r"\Aorder_[A-Za-z0-9]+\Z", max_length=64)
    razorpay_payment_id = serializers.RegexField(r"\Apay_[A-Za-z0-9]+\Z", max_length=64)
    razorpay_signature = serializers.RegexField(r"\A[0-9a-f]{64}\Z", write_only=True, trim_whitespace=False)


class PaymentThrottle(UserRateThrottle):
    scope = "customer_payment"
    rate = "15/min"


class PaymentBaseView(generics.GenericAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [CustomerOnly]
    parser_classes = [JSONParser]
    throttle_classes = [PaymentThrottle]
    serializer_class = PaymentRequestSerializer

    @transaction.non_atomic_requests
    def dispatch(self, request, *args, **kwargs):
        protect_handover_request(request)
        return super().dispatch(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response.sensitive_response = True
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        patch_vary_headers(response, ["Authorization"])
        return response

    def handle_exception(self, exc):
        if isinstance(exc, GatewayError):
            return Response({"code": exc.code, "detail": exc.detail}, status=exc.status_code)
        return super().handle_exception(exc)


class PaymentInitiateView(PaymentBaseView):
    @sensitive_variables()
    def post(self, request, pk):
        payload = self.get_serializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data, code = initiate_payment(request.user, pk)
        return Response(data, status=code)


class PaymentVerifyView(PaymentBaseView):
    serializer_class = PaymentVerifySerializer

    @sensitive_variables()
    def post(self, request, pk):
        payload = self.get_serializer(data=request.data)
        payload.is_valid(raise_exception=True)
        values = payload.validated_data
        data, code = verify_customer_payment(
            request.user, pk, values["razorpay_order_id"],
            values["razorpay_payment_id"], values["razorpay_signature"],
        )
        return Response(data, status=code)


class PaymentReconcileView(PaymentBaseView):
    @sensitive_variables()
    def post(self, request, pk):
        payload = self.get_serializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data, code = reconcile_payment(request.user, pk)
        return Response(data, status=code)


class RazorpayWebhookView(PaymentBaseView):
    # Authentication here is HMAC over raw bytes, not a customer JWT or cookies.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []  # Provider retries must not depend on a user's cache bucket.

    @sensitive_variables()
    def post(self, request):
        data, code = handle_webhook(
            request.body, request.headers.get("X-Razorpay-Signature", ""),
            request.headers.get("X-Razorpay-Event-Id", ""),
        )
        return Response(data, status=code)
