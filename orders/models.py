import uuid

from django.conf import settings
from django.db import models


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("pending_super_admin", "Pending Super Admin"),
        ("admin_approved", "Admin Approved"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
        ("confirmed", "Confirmed"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    PAYMENT_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
    ]

    PAYMENT_METHOD_CHOICES = [
        ("online", "Online"),
    ]

    PICKUP_MODE_CHOICES = [
        ("walk_in", "Walk In"),
        ("vehicle", "Vehicle"),
    ]

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )

    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.PROTECT,
        related_name="orders",
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="pending",
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="online",
    )

    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default="pending",
    )

    pickup_mode = models.CharField(
        max_length=20,
        choices=PICKUP_MODE_CHOICES,
        default="walk_in",
    )

    vehicle = models.ForeignKey(
        "customers.CustomerVehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    application_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default="50.00",
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    decided_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_order_decisions",
    )
    reject_reason_code = models.CharField(max_length=40, blank=True)
    super_admin_approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    super_admin_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_high_value_orders",
    )

    # Pickup verification
    pickup_verified = models.BooleanField(
        default=False,
    )

    pickup_verified_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    pickup_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_pickups",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return f"Order #{self.id} - {self.customer}"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="order_items",
    )

    product_name = models.CharField(
        max_length=200,
    )

    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    quantity = models.PositiveIntegerField()

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"


class OrderLiveLocation(models.Model):
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="live_location",
    )
    publisher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_order_locations",
    )
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
    is_sharing = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Live location for order #{self.order_id}"

class OrderHandoverOTP(models.Model):
    """One current challenge per order; never stores a plaintext code."""

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="handover_otp")
    challenge_id = models.UUIDField(unique=True)
    ciphertext = models.BinaryField(null=True, editable=False)
    verification_digest = models.CharField(max_length=64, blank=True, editable=False)
    key_version = models.CharField(max_length=32)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    reissue_available_at = models.DateTimeField()
    issue_window_started_at = models.DateTimeField()
    issue_count = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(failed_attempts__gte=0, failed_attempts__lte=5), name="handover_attempts_0_5"),
            models.CheckConstraint(condition=models.Q(expires_at__gt=models.F("issued_at")), name="handover_expiry_after_issue"),
        ]


class ImmutableHandoverAuditQuerySet(models.QuerySet):
    """Application-level audit records are append-only."""

    def update(self, **kwargs):
        from django.core.exceptions import ValidationError

        raise ValidationError("Handover audit records are immutable.")

    def delete(self):
        from django.core.exceptions import ValidationError

        raise ValidationError("Handover audit records are immutable.")


class ImmutableHandoverAuditManager(models.Manager.from_queryset(ImmutableHandoverAuditQuerySet)):
    pass


class HandoverAuditEvent(models.Model):
    """Nonsecret lifecycle events; no submitted code or cryptographic material."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="handover_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    challenge_id = models.UUIDField()
    event_type = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableHandoverAuditManager()

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk is not None:
            raise ValidationError("Handover audit records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        raise ValidationError("Handover audit records are immutable.")


class CustomerHandoverLocation(models.Model):
    """Optional short-lived foreground evidence; coordinates cleared on terminal state."""
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="handover_location")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    captured_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now=True)


class StaffHandoverConfirmation(models.Model):
    """One append-only, non-OTP completion record per order."""
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="staff_handover")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    confirmed_at = models.DateTimeField(auto_now_add=True)
    pickup_mode = models.CharField(max_length=20, choices=Order.PICKUP_MODE_CHOICES)
    customer_id_checked = models.PositiveBigIntegerField()
    staff_distance_m = models.DecimalField(max_digits=9, decimal_places=2)
    staff_location_captured_at = models.DateTimeField()
    customer_distance_m = models.DecimalField(max_digits=9, decimal_places=2, null=True)
    customer_location_status = models.CharField(max_length=20)
    acknowledgments = models.JSONField()
    objects = ImmutableHandoverAuditManager()

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk is not None:
            raise ValidationError("Staff handover confirmations are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError("Staff handover confirmations are immutable.")


class ShopPaymentAccount(models.Model):
    """Operator-approved Razorpay Route mapping; never holds bank details or keys."""

    shop = models.OneToOneField("shops.Shop", on_delete=models.PROTECT, related_name="payment_account")
    provider_account_id = models.CharField(max_length=64, unique=True)
    is_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class OrderPayment(models.Model):
    """One immutable amount/Route destination per order; provider calls occur outside locks."""

    STATUS_CHOICES = [
        ("creating", "Creating"), ("created", "Awaiting Payment"),
        ("captured", "Captured"), ("refund_required", "Refund Required"),
        ("refunding", "Refunding"), ("refunded", "Refunded"),
        ("review", "Reconciliation Required"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="payment")
    # This UUID is also the nonsecret, stable provider receipt (32 characters).
    amount = models.PositiveBigIntegerField(help_text="Total in paise.")
    shop_amount = models.PositiveBigIntegerField(help_text="Product subtotal in paise.")
    platform_fee = models.PositiveBigIntegerField(help_text="Platform fee in paise; included in amount.")
    currency = models.CharField(max_length=3, default="INR")
    provider_account_id = models.CharField(max_length=64)
    key_id = models.CharField(max_length=64)
    provider_order_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    provider_payment_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    provider_refund_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="creating")
    captured_at = models.DateTimeField(null=True, blank=True)
    refunded_amount = models.PositiveBigIntegerField(default=0)
    refund_started_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_positive_amount"),
            models.CheckConstraint(condition=models.Q(amount=models.F("shop_amount") + models.F("platform_fee")), name="payment_amount_split"),
            models.CheckConstraint(condition=models.Q(currency="INR"), name="payment_inr_only"),
            models.CheckConstraint(condition=models.Q(refunded_amount__lte=models.F("amount")), name="payment_refund_within_amount"),
        ]


class PaymentWebhookEvent(models.Model):
    """Deduplication metadata only: no raw payload, customer details or signature."""

    event_id = models.CharField(max_length=128, unique=True)
    payload_digest = models.CharField(max_length=64)
    event_type = models.CharField(max_length=64)
    payment = models.ForeignKey(OrderPayment, on_delete=models.PROTECT, related_name="webhook_events")
    processed_at = models.DateTimeField(auto_now_add=True)


class PaymentRefund(models.Model):
    """Provider-confirmed or review-held refund attempt; amount is always paise."""

    STATUS_CHOICES = [
        ("requested", "Requested"), ("pending", "Pending"),
        ("processed", "Processed"), ("failed", "Failed"),
        ("review", "Review Required"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(OrderPayment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="requested")
    provider_refund_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reason_code = models.CharField(max_length=64, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="refund_positive_amount"),
            models.UniqueConstraint(fields=["payment", "amount"], name="unique_payment_refund_amount"),
        ]
        ordering = ["created_at", "id"]


class PaymentOperationalCase(models.Model):
    """No-PII operational exception ledger; resolved cases remain audit evidence."""

    KIND_CHOICES = [
        ("reconciliation", "Reconciliation"), ("refund", "Refund"),
        ("settlement", "Settlement"), ("dispute", "Dispute"),
    ]
    STATUS_CHOICES = [("open", "Open"), ("review", "Review"), ("resolved", "Resolved")]
    payment = models.ForeignKey(OrderPayment, on_delete=models.PROTECT, related_name="operational_cases")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="open")
    external_reference = models.CharField(max_length=64, blank=True)
    reason_code = models.CharField(max_length=64)
    details = models.JSONField(default=dict, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="opened_payment_cases")
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_payment_cases")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "status"])]
        ordering = ["-created_at", "-id"]
