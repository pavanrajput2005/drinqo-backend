from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from .models import UserPushToken


def register_push_token(*, user, token, platform):
    """Idempotently register a token without allowing another user to take it."""
    with transaction.atomic():
        device, created = UserPushToken.objects.select_for_update().get_or_create(
            token=token, defaults={"user": user, "platform": platform},
        )
        if device.user_id != user.pk:
            raise PermissionDenied(
                "This device token is already registered to another account."
            )
        if device.platform != platform:
            device.platform = platform
            device.save(update_fields=["platform", "updated_at"])
        return device, created
