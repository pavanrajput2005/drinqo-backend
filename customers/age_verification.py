from datetime import date

from django.utils import timezone

from .models import CustomerVerificationSession
from .verification_constants import AGE_PROOF_DOCUMENT_TYPE
MINIMUM_CUSTOMER_AGE = 21


def is_at_least_minimum_age(date_of_birth, today=None):
    if not isinstance(date_of_birth, date):
        return False

    today = today or timezone.localdate()
    age = today.year - date_of_birth.year - (
        (today.month, today.day) < (date_of_birth.month, date_of_birth.day)
    )
    return age >= MINIMUM_CUSTOMER_AGE


def has_approved_age_proof(user):
    if user.date_of_birth is None:
        return False

    # Only the primary key is needed for the linked audit lookup. Selecting a
    # full model row would unnecessarily require newly added document fields
    # (for example consent_at) on installations still awaiting migration 0008.
    session_id = CustomerVerificationSession.objects.filter(
        user_id=user.pk,
        document_type=AGE_PROOF_DOCUMENT_TYPE,
        status="approved",
        reviewed_by__isnull=False,
        reviewed_at__isnull=False,
    ).order_by("-reviewed_at", "-id").values_list("id", flat=True).first()
    if session_id is None:
        return False

    from shop_admin.models import SuperAdminAuditLog

    return SuperAdminAuditLog.objects.filter(
        action="customer_document_approved",
        resource_type="verification_session",
        resource_id=session_id,
        metadata__age_proof=True,
        metadata__confirmed_date_of_birth=user.date_of_birth.isoformat(),
    ).exists()


def is_age_verified(user):
    return bool(
        user.age_verified
        and has_approved_age_proof(user)
        and is_at_least_minimum_age(user.date_of_birth)
    )


def is_age_self_declared(user):
    """Persisted acknowledgement; it is not documentary identity proof."""
    return bool(
        user.age_self_declared
        and user.age_self_declared_at is not None
        and is_at_least_minimum_age(user.date_of_birth)
    )


def age_proof_status(user):
    if is_age_verified(user):
        return "approved"

    return (
        CustomerVerificationSession.objects.filter(
            user_id=user.pk,
            document_type=AGE_PROOF_DOCUMENT_TYPE,
        )
        .order_by("-created_at", "-id")
        .values_list("status", flat=True)
        .first()
        or "proof_required"
    )
