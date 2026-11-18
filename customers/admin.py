from django.contrib import admin

from .models import CustomerProfile, CustomerVehicle, FavoriteShop


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "user",
        "phone",
        "email",
        "city",
        "postal_code",
        "profile_completed_at",
    )
    search_fields = (
        "full_name",
        "user__email",
        "phone",
        "email",
        "city",
        "postal_code",
    )
    list_filter = ("city",)


@admin.register(CustomerVehicle)
class CustomerVehicleAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "vehicle_number",
        "vehicle_type",
    )
    search_fields = (
        "customer__email",
        "vehicle_number",
    )


@admin.register(FavoriteShop)
class FavoriteShopAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "shop",
        "created_at",
    )
    search_fields = (
        "customer__email",
        "shop__name",
    )