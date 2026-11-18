from django.db.models.signals import post_save
from django.db import transaction
from django.dispatch import receiver

from .models import Order, OrderLiveLocation, OrderHandoverOTP, OrderPayment, CustomerHandoverLocation


@receiver(post_save, sender=Order)
def stop_location_sharing_for_finished_order(sender, instance, **kwargs):
    if instance.status in {"completed", "cancelled", "rejected"}:
        CustomerHandoverLocation.objects.filter(order=instance).update(latitude=None, longitude=None)
        OrderHandoverOTP.objects.filter(order=instance).update(
            ciphertext=None, verification_digest="",
        )
        OrderLiveLocation.objects.filter(
            order=instance,
            is_sharing=True,
        ).update(
            is_sharing=False,
            latitude=None,
            longitude=None,
        )
    if instance.status in {"cancelled", "rejected"}:
        queued = OrderPayment.objects.filter(order=instance, status="captured").update(status="refund_required")
        if queued:
            from .payments import try_refund_order
            # This runs only AFTER the inventory/cancellation transaction commits.
            transaction.on_commit(lambda order_id=instance.pk: try_refund_order(order_id))
