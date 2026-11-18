from rest_framework.permissions import BasePermission


class CustomerOnly(BasePermission):
    message = "Customer access required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "user_type", None) == "customer"
        )
