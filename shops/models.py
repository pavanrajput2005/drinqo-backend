from django.conf import settings
from django.db import models
from django.db.models import Q


class Shop(models.Model):
    LICENSE_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("verified", "Verified"),
        ("rejected", "Rejected"),
        ("expired", "Expired"),
    ]

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_shop",
        limit_choices_to={"user_type": "shop_admin"},
    )

    shop_name = models.CharField(max_length=200)

    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True)

    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)

    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=10)

    # Shop GPS location
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    license_number = models.CharField(
        max_length=100,
        unique=True,
    )

    license_status = models.CharField(
        max_length=20,
        choices=LICENSE_STATUS_CHOICES,
        default="pending",
    )

    is_active = models.BooleanField(default=False)
    is_open = models.BooleanField(default=True)
    is_suspended_by_super_admin = models.BooleanField(default=False)
    opening_hours = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.shop_name


class ShopLicenseDocument(models.Model):
    VERIFICATION_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("verified", "Verified"),
        ("rejected", "Rejected"),
    ]

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="application_documents",
    )
    license_number = models.CharField(max_length=100)
    document = models.FileField(upload_to="private_shop_licenses/")
    expiry_date = models.DateField()
    verification_status = models.CharField(
        max_length=20,
        choices=VERIFICATION_STATUS_CHOICES,
        default="pending",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.license_number} ({self.shop_id})"


class ShopLocationChangeRequest(models.Model):
    """A reviewed proposal; Shop.latitude/longitude remain the approved value."""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    shop = models.ForeignKey(
        Shop,
        on_delete=models.PROTECT,
        related_name="location_change_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="shop_location_change_requests",
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    reason = models.TextField(max_length=500)
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    previous_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    previous_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_shop_location_change_requests",
    )
    review_reason = models.TextField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop"],
                condition=Q(status="pending"),
                name="one_pending_shop_location_request",
            ),
            models.CheckConstraint(
                condition=Q(latitude__gte=-90) & Q(latitude__lte=90),
                name="shop_location_request_latitude_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__gte=-180) & Q(longitude__lte=180),
                name="shop_location_request_longitude_range",
            ),
        ]
        indexes = [
            models.Index(fields=["shop", "status", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Location request {self.pk} for shop {self.shop_id}"

    def save(self, *args, **kwargs):
        """A reviewed decision is append-only history, not an editable record."""
        if self.pk is not None:
            original = type(self)._base_manager.filter(pk=self.pk).values("status").first()
            if original and original["status"] != self.STATUS_PENDING:
                from django.core.exceptions import ValidationError
                raise ValidationError("Reviewed location requests are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError("Location change requests are retained as audit history.")
