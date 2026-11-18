from django.conf import settings
from django.db import models


class ImmutableAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        from django.core.exceptions import ValidationError

        raise ValidationError("Audit log records are immutable.")

    def delete(self):
        from django.core.exceptions import ValidationError

        raise ValidationError("Audit log records are immutable.")


class ImmutableAuditManager(models.Manager.from_queryset(ImmutableAuditQuerySet)):
    pass


class ShopAdminProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shop_admin_profile",
    )
    shop_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.shop_name

class Shop(models.Model):
    admin = models.OneToOneField(
        ShopAdminProfile,
        on_delete=models.CASCADE,
        related_name="shop",
    )
    name = models.CharField(max_length=150)
    address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ShopLicenseDocument(models.Model):
    VERIFICATION_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("verified", "Verified"),
        ("rejected", "Rejected"),
    ]

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="license_documents",
    )
    license_number = models.CharField(max_length=100)
    document = models.FileField(upload_to="shop_licenses/")
    expiry_date = models.DateField()
    verification_status = models.CharField(
        max_length=20,
        choices=VERIFICATION_STATUS_CHOICES,
        default="pending",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.license_number


class ShopAdminNotification(models.Model):
    NOTIFICATION_TYPES = [
        ("new_order", "New Order"),
        ("order_update", "Order Update"),
        ("low_stock", "Low Stock"),
        ("system", "System"),
    ]

    shop_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shop_admin_notifications",
    )
    notification_type = models.CharField(
        max_length=30,
        choices=NOTIFICATION_TYPES,
        default="system",
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    order_id = models.PositiveIntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} - {self.shop_admin}"


class SuperAdminAuditLog(models.Model):
    objects = ImmutableAuditManager()

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="super_admin_audit_logs",
    )
    actor_email = models.EmailField()
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=50)
    resource_id = models.PositiveIntegerField()
    summary = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk is not None:
            raise ValidationError("Audit log records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        raise ValidationError("Audit log records are immutable.")

    def __str__(self):
        return f"{self.action} ({self.resource_type}:{self.resource_id})"


class PlatformSettings(models.Model):
    high_value_order_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default="15000.00",
    )
    nearby_shop_radius_km = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default="10.00",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_platform_settings",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "platform settings"
