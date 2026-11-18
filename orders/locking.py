"""Shared row-lock ordering. Call only inside transaction.atomic()."""
from django.shortcuts import get_object_or_404
from shops.models import Shop
from .models import Order


def lock_order_with_shop(order_id, **scope):
    # Recheck the entire scope after locking: ownership/state may have changed.
    shop_id = get_object_or_404(Order.objects.only("shop_id"), pk=order_id, **scope).shop_id
    shop = get_object_or_404(Shop.objects.select_for_update(of=("self",)), pk=shop_id)
    order = get_object_or_404(
        Order.objects.select_for_update(of=("self",)), pk=order_id, shop_id=shop_id, **scope,
    )
    order.shop = shop
    return order
