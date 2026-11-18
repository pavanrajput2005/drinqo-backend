"""Additive payment ledger only. No historical data is relabelled or backfilled."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0009_orderhandoverotp_handoverauditevent")]

    operations = [
        migrations.CreateModel(
            name="ShopPaymentAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider_account_id", models.CharField(max_length=64, unique=True)),
                ("is_enabled", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("shop", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="payment_account", to="shops.shop")),
            ],
        ),
        migrations.CreateModel(
            name="OrderPayment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("amount", models.PositiveBigIntegerField(help_text="Total in paise.")),
                ("shop_amount", models.PositiveBigIntegerField(help_text="Product subtotal in paise.")),
                ("platform_fee", models.PositiveBigIntegerField(help_text="Platform fee in paise; included in amount.")),
                ("currency", models.CharField(default="INR", max_length=3)),
                ("provider_account_id", models.CharField(max_length=64)),
                ("key_id", models.CharField(max_length=64)),
                ("provider_order_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("provider_payment_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("provider_refund_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("status", models.CharField(choices=[
                    ("creating", "Creating"), ("created", "Awaiting Payment"),
                    ("captured", "Captured"), ("refund_required", "Refund Required"),
                    ("refunding", "Refunding"), ("refunded", "Refunded"),
                    ("review", "Reconciliation Required"),
                ], default="creating", max_length=24)),
                ("captured_at", models.DateTimeField(blank=True, null=True)),
                ("refunded_amount", models.PositiveBigIntegerField(default=0)),
                ("refund_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="payment", to="orders.order")),
            ],
            options={"constraints": [
                models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_positive_amount"),
                models.CheckConstraint(condition=models.Q(amount=models.F("shop_amount") + models.F("platform_fee")), name="payment_amount_split"),
                models.CheckConstraint(condition=models.Q(currency="INR"), name="payment_inr_only"),
                models.CheckConstraint(condition=models.Q(refunded_amount__lte=models.F("amount")), name="payment_refund_within_amount"),
            ]},
        ),
        migrations.CreateModel(
            name="PaymentWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=128, unique=True)),
                ("payload_digest", models.CharField(max_length=64)),
                ("event_type", models.CharField(max_length=64)),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="webhook_events", to="orders.orderpayment")),
            ],
        ),
    ]
