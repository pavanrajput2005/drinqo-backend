from django.contrib import admin

from .models import Shop, ShopLicenseDocument


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = (
        "shop_name",
        "owner",
        "city",
        "state",
        "license_status",
        "is_active",
        "is_suspended_by_super_admin",
        "created_at",
    )
    list_filter = (
        "license_status",
        "is_active",
        "is_suspended_by_super_admin",
        "state",
    )
    search_fields = (
        "shop_name",
        "owner__email",
        "owner__first_name",
        "owner__last_name",
        "phone",
        "license_number",
        "city",
        "state",
    )
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("owner",)
    ordering = ("-created_at", "-id")


@admin.register(ShopLicenseDocument)
class ShopLicenseDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "license_number",
        "shop",
        "expiry_date",
        "verification_status",
        "verified_at",
        "created_at",
    )
    list_filter = ("verification_status", "expiry_date")
    search_fields = (
        "license_number",
        "shop__shop_name",
        "shop__owner__email",
    )
    readonly_fields = ("verified_at", "created_at")
    list_select_related = ("shop", "shop__owner")
    ordering = ("-created_at", "-id")
