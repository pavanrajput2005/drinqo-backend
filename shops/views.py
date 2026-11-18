from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from shop_admin.models import PlatformSettings

from .models import Shop
from .permissions import (
    IsApprovedActiveShopAdmin,
    shops_accepting_new_orders,
)
from .serializers import (
    CustomerShopSerializer,
    NearbyShopCoordinatesSerializer,
    ShopSerializer,
)
from .geofence import distance_between_coordinates
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

class ShopDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ShopSerializer
    permission_classes = [IsAuthenticated, IsApprovedActiveShopAdmin]

    def get_object(self):
        return Shop.objects.get(owner=self.request.user)


class CustomerShopListView(generics.ListAPIView):
    serializer_class = CustomerShopSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return shops_accepting_new_orders().order_by("shop_name")


class CustomerShopDetailView(generics.RetrieveAPIView):
    serializer_class = CustomerShopSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return shops_accepting_new_orders()


class NearbyShopListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="latitude",
                type=OpenApiTypes.FLOAT,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Customer current latitude",
            ),
            OpenApiParameter(
                name="longitude",
                type=OpenApiTypes.FLOAT,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Customer current longitude",
            ),
        ]
    )
    def get(self, request, *args, **kwargs):
        coordinates = NearbyShopCoordinatesSerializer(
            data=request.query_params
        )
        coordinates.is_valid(raise_exception=True)
        customer_lat = coordinates.validated_data["latitude"]
        customer_lon = coordinates.validated_data["longitude"]

        shops = shops_accepting_new_orders().filter(
            latitude__isnull=False,
            longitude__isnull=False,
        )
        platform_settings, _ = PlatformSettings.objects.get_or_create(pk=1)
        radius_km = float(platform_settings.nearby_shop_radius_km)

        nearby_shops = []

        for shop in shops:
            distance_km = distance_between_coordinates(
                customer_lat,
                customer_lon,
                shop.latitude,
                shop.longitude,
            ) / 1000

            if distance_km <= radius_km:
                data = CustomerShopSerializer(shop).data
                data["distance_km"] = round(distance_km, 2)
                nearby_shops.append(data)

        nearby_shops.sort(key=lambda shop: shop["distance_km"])

        return Response(
            {
                "count": len(nearby_shops),
                "radius_km": radius_km,
                "shops": nearby_shops,
            },
            status=status.HTTP_200_OK,
        )
    
