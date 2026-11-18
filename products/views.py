from django.db import models

from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
)

from .models import Product
from .serializers import CustomerProductSerializer
from shops.permissions import shops_accepting_new_orders


class CustomerProductListView(generics.ListAPIView):
    serializer_class = CustomerProductSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search product by name or brand.",
            ),
            OpenApiParameter(
                name="category",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by category: beer, whisky, vodka, rum, wine, other.",
            ),
        ]
    )
    def get_queryset(self):
        queryset = Product.objects.filter(
            shop__in=shops_accepting_new_orders(),
            is_active=True,
        ).select_related("shop").order_by("name")

        search = self.request.query_params.get("search")
        category = self.request.query_params.get("category")

        if search:
            queryset = queryset.filter(
                models.Q(name__icontains=search)
                | models.Q(brand__icontains=search)
            )

        if category:
            queryset = queryset.filter(
                category=category.lower()
            )

        return queryset


class CustomerProductDetailView(generics.RetrieveAPIView):
    serializer_class = CustomerProductSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Product.objects.filter(
            shop__in=shops_accepting_new_orders(),
            is_active=True,
        ).select_related("shop")


class CustomerShopProductListView(generics.ListAPIView):
    serializer_class = CustomerProductSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="shop_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="Shop ID.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search product by name or brand within this shop.",
            ),
            OpenApiParameter(
                name="category",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by category.",
            ),
        ]
    )
    def get_queryset(self):
        shop_id = self.kwargs["shop_id"]

        queryset = Product.objects.filter(
            shop_id=shop_id,
            shop__in=shops_accepting_new_orders(),
            is_active=True,
        ).select_related("shop").order_by("name")

        search = self.request.query_params.get("search")
        category = self.request.query_params.get("category")

        if search:
            queryset = queryset.filter(
                models.Q(name__icontains=search)
                | models.Q(brand__icontains=search)
            )

        if category:
            queryset = queryset.filter(
                category=category.lower()
            )

        return queryset