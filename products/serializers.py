from rest_framework import serializers

from .models import Product


class CustomerProductSerializer(serializers.ModelSerializer):
    shop_id = serializers.IntegerField(
        source="shop.id",
        read_only=True,
    )

    shop_name = serializers.CharField(
        source="shop.shop_name",
        read_only=True,
    )

    class Meta:
        model = Product

        fields = [
            "id",
            "shop_id",
            "shop_name",
            "name",
            "brand",
            "category",
            "description",
            "price",
            "stock_quantity",
            "image",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "shop_id",
            "shop_name",
            "price",
            "stock_quantity",
            "is_active",
            "created_at",
            "updated_at",
        ]