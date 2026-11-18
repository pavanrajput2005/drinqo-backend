"""Authenticated handover APIs. Never include bodies in logs or telemetry."""
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import generics
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated

from accounts.authentication import JWTAuthentication
from .handover_privacy import protect_handover_request


@method_decorator(sensitive_post_parameters("otp"), name="dispatch")
class HandoverBaseView(generics.GenericAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def dispatch(self, request, *args, **kwargs):
        protect_handover_request(request)
        return super().dispatch(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response.sensitive_response = True
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        patch_vary_headers(response, ["Authorization"])
        if isinstance(response.data, dict) and "retry_after" in response.data:
            response["Retry-After"] = str(response.data["retry_after"])
        return response
