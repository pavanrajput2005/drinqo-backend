from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0008_alter_order_application_fee"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderHandoverOTP",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("challenge_id", models.UUIDField(unique=True)),
                ("ciphertext", models.BinaryField(editable=False, null=True)),
                ("verification_digest", models.CharField(blank=True, editable=False, max_length=64)),
                ("key_version", models.CharField(max_length=32)),
                ("issued_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("failed_attempts", models.PositiveSmallIntegerField(default=0)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("reissue_available_at", models.DateTimeField()),
                ("issue_window_started_at", models.DateTimeField()),
                ("issue_count", models.PositiveSmallIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="handover_otp", to="orders.order")),
            ],
            options={"constraints": [
                models.CheckConstraint(condition=models.Q(failed_attempts__gte=0, failed_attempts__lte=5), name="handover_attempts_0_5"),
                models.CheckConstraint(condition=models.Q(expires_at__gt=models.F("issued_at")), name="handover_expiry_after_issue"),
            ]},
        ),
        migrations.CreateModel(
            name="HandoverAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("challenge_id", models.UUIDField()),
                ("event_type", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="handover_events", to="orders.order")),
            ],
        ),
    ]
