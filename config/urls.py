from django.contrib import admin
from django.urls import include, path
from accounts.mobile_views import MobileProfileView
from config.health import health

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("health", health, name="health"),
    path("api/auth/", include("accounts.mobile_urls")),
    path("api/user/profile/", MobileProfileView.as_view(), name="mobile-user-profile"),
    path("admin/", admin.site.urls),

    # API Documentation
    path(
        "api/schema/",
        SpectacularAPIView.as_view(),
        name="schema",
    ),

    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),

    # DRINQO APIs
    path(
        "api/accounts/",
        include("accounts.urls"),
    ),

    path(
        "api/shop-admin/",
        include("shop_admin.urls"),
    ),

    path(
        "api/super-admin/",
        include("shop_admin.super_admin_urls"),
    ),

    path(
        "api/customer/",
        include("customers.urls"),
    ),

    path(
        "api/shop/",
        include("shops.urls"),
    ),

    path(
        "api/products/",
        include("products.urls"),
    ),

    path(
        "api/orders/",
        include("orders.urls"),
    ),
]
