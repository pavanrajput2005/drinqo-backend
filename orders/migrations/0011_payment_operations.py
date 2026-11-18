"""Additive payment operations records; no historical financial data is changed."""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0010_payment_ledger"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentRefund",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("amount", models.PositiveBigIntegerField()),
                ("status", models.CharField(choices=[("requested", "Requested"), ("pending", "Pending"), ("processed", "Processed"), ("failed", "Failed"), ("review", "Review Required")], default="requested", max_length=16)),
                ("provider_refund_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("reason_code", models.CharField(blank=True, max_length=64)),
                ("attempted_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refunds", to="orders.orderpayment")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(model_name="paymentrefund", constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="refund_positive_amount")),
        migrations.AddConstraint(model_name="paymentrefund", constraint=models.UniqueConstraint(fields=("payment", "amount"), name="unique_payment_refund_amount")),
        migrations.CreateModel(
            name="PaymentOperationalCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("reconciliation", "Reconciliation"), ("refund", "Refund"), ("settlement", "Settlement"), ("dispute", "Dispute")], max_length=20)),
                ("status", models.CharField(choices=[("open", "Open"), ("review", "Review"), ("resolved", "Resolved")], default="open", max_length=16)),
                ("external_reference", models.CharField(blank=True, max_length=64)),
                ("reason_code", models.CharField(max_length=64)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("opened_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="opened_payment_cases", to=settings.AUTH_USER_MODEL)),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operational_cases", to="orders.orderpayment")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_payment_cases", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(model_name="paymentoperationalcase", index=models.Index(fields=["kind", "status"], name="orders_pay_kind_status_idx")),
    ]
