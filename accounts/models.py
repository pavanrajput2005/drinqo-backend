from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils.crypto import salted_hmac, constant_time_compare
from django.utils import timezone
from datetime import timedelta

class User(AbstractUser):
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=False)
    phone = models.CharField(max_length=15, blank=True)
    firebase_uid = models.CharField(
        max_length=128,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    google_sub = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    is_verified = models.BooleanField(default=False)
    date_of_birth = models.DateField(null=True, blank=True)
    age_verified = models.BooleanField(default=False)
    age_verified_at = models.DateTimeField(null=True, blank=True)
    age_self_declared = models.BooleanField(default=False)
    age_self_declared_at = models.DateTimeField(null=True, blank=True)
    is_blocked = models.BooleanField(default=False)
    birth_date = models.DateField(null=True, blank=True)
    mobile_number = models.CharField(max_length=15, null=True, blank=True)
    auth_provider = models.CharField(
        max_length=10,
        choices=(("google", "Google"), ("apple", "Apple"), ("email", "Email")),
        default="email",
    )
    is_profile_complete = models.BooleanField(default=False)
    apple_sub = models.CharField(max_length=255, unique=True, null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]
    USER_TYPE_CHOICES = [
        ("customer", "Customer"),
        ("shop_admin", "Shop Admin"),
        ("super_admin", "Super Admin"),
    ]
    user_type = models.CharField(
        max_length=20, choices=USER_TYPE_CHOICES, default="customer",
    )

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def save(self, *args, **kwargs):
        # Preserve the existing DRINQO field names while exposing the new mobile
        # contract. Legacy writes remain authoritative unless explicitly replaced.
        fields = kwargs.get("update_fields")
        fields = set(fields) if fields is not None else None
        if fields is not None and not fields:
            return
        previous = None
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "date_of_birth", "birth_date", "phone", "mobile_number"
            ).first()
        for legacy, mobile in (("date_of_birth", "birth_date"), ("phone", "mobile_number")):
            mobile_changed = (
                mobile in fields if fields is not None else
                (getattr(self, mobile) != previous[mobile] if previous else bool(getattr(self, mobile)))
            )
            legacy_changed = (
                legacy in fields if fields is not None else
                (getattr(self, legacy) != previous[legacy] if previous else bool(getattr(self, legacy)))
            )
            if mobile_changed and not legacy_changed:
                setattr(self, legacy, getattr(self, mobile) or ("" if legacy == "phone" else None))
                if fields is not None:
                    fields.add(legacy)
            elif legacy_changed:
                setattr(self, mobile, getattr(self, legacy) or None)
                if fields is not None:
                    fields.add(mobile)
        self.email = self.email.strip().lower()
        if fields is not None:
            kwargs["update_fields"] = fields
        super().save(*args, **kwargs)


class EmailOTP(models.Model):
    email = models.EmailField(unique=True)
    # HMAC digest only: never persist the six-digit plaintext login secret.
    otp_code = models.CharField(max_length=64)
    purpose = models.CharField(max_length=10, default="login", choices=[("login", "Login"), ("link", "Link email")])
    requesting_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def digest(self, code):
        return salted_hmac(
            "drinqo-email-otp", f"{self.email}:{self.purpose}:{self.requesting_user_id}:{code}",
            secret=settings.EMAIL_OTP_HMAC_KEY, algorithm="sha256",
        ).hexdigest()

    def set_code(self, code):
        self.otp_code = self.digest(code)

    def check_code(self, code):
        return constant_time_compare(self.otp_code, self.digest(code))

    def is_valid(self):
        return self.created_at is not None and timedelta(0) <= timezone.now() - self.created_at < timedelta(minutes=5)


class EmailAuthState(models.Model):
    """Durable per-address limits; retained after OTP consumption/resends."""

    email_digest = models.CharField(max_length=64, unique=True)
    send_count = models.PositiveSmallIntegerField(default=0)
    verify_count = models.PositiveSmallIntegerField(default=0)
    send_window = models.DateTimeField(null=True, blank=True)
    verify_window = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

class OtpPhoneState(models.Model):
    """Pseudonymous, durable throttling state for an OTP phone number."""

    phone_hash = models.CharField(max_length=64, unique=True)
    send_count = models.PositiveSmallIntegerField(default=0)
    send_window_started_at = models.DateTimeField(null=True, blank=True)
    verify_count = models.PositiveSmallIntegerField(default=0)
    verify_window_started_at = models.DateTimeField(null=True, blank=True)
    resend_available_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)


class ConsumedOtp(models.Model):
    """One-time proof consumption ledger; contains no raw phone or OTP."""

    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class VerifiedPhoneIdentity(models.Model):
    """Canonical phone-login ownership, stored without the raw phone number."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="verified_phone_identity",
    )
    phone_hash = models.CharField(max_length=64, unique=True)
    phone_last4 = models.CharField(max_length=4)
    verified_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Verified phone ending {self.phone_last4}"
