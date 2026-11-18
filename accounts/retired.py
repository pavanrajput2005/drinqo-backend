"""Stable retirement responses. No provider calls, account writes, or JWTs."""
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiResponse, extend_schema


class RetiredPhoneOTPView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=None, deprecated=True, responses={410: OpenApiResponse(description="Phone/SMS authentication retired; no tokens are issued.")})
    def post(self, request):
        return Response({
            "code": "phone_otp_retired",
            "error": "Phone/SMS OTP is retired. Use email/password, email OTP, Google or Apple. Existing signed-in users can link a verified email; otherwise contact support for account recovery.",
        }, status=410, headers={"Cache-Control": "no-store"})
