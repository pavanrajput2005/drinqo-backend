from rest_framework.exceptions import APIException
from rest_framework.throttling import ScopedRateThrottle


class RateLimitUnavailable(APIException):
    status_code = 503
    default_detail = "Authentication is temporarily unavailable."
    default_code = "rate_limit_unavailable"


class FailClosedLoginRateThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            raise RateLimitUnavailable() from None
