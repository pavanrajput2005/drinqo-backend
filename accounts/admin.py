from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    readonly_fields = ("date_of_birth", "age_verified", "age_verified_at")

    fieldsets = UserAdmin.fieldsets + (
        ("DRINQO Details", {
            "fields": (
                "phone",
                "is_verified",
                "date_of_birth",
                "age_verified",
                "age_verified_at",
                "user_type",
            ),
        }),
    )