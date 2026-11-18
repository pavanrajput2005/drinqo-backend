from django.urls import path

from .views import (
    ShopDetailView,
    CustomerShopListView,
    CustomerShopDetailView,
    NearbyShopListView,
)


urlpatterns = [
    path(
        "profile/",
        ShopDetailView.as_view(),
        name="shop-profile",
    ),

    path(
        "list/",
        CustomerShopListView.as_view(),
        name="customer-shop-list",
    ),

    path(
        "nearby/",
        NearbyShopListView.as_view(),
        name="nearby-shops",
    ),

    path(
        "<int:pk>/",
        CustomerShopDetailView.as_view(),
        name="customer-shop-detail",
    ),
]