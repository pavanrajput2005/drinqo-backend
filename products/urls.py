from django.urls import path

from .views import (
    CustomerProductListView,
    CustomerProductDetailView,
    CustomerShopProductListView,
)


urlpatterns = [
    path(
        "",
        CustomerProductListView.as_view(),
        name="customer-product-list",
    ),
    path(
        "shop/<int:shop_id>/",
        CustomerShopProductListView.as_view(),
        name="customer-shop-product-list",
    ),
    path(
        "<int:pk>/",
        CustomerProductDetailView.as_view(),
        name="customer-product-detail",
    ),
]