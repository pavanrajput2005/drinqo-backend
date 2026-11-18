from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import (
    JWTAuthentication as SimpleJWTAuthentication,
)


class JWTAuthentication(SimpleJWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        if user.is_blocked:
            raise PermissionDenied("This account is blocked.")

        return user, token
