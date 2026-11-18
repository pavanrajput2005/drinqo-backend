"""Shared customer eligibility policy for checkout, order creation, and payment."""

from dataclasses import dataclass

from .age_verification import (
    is_age_self_declared,
    is_age_verified,
    is_at_least_minimum_age,
)
from .models import CustomerProfile


@dataclass(frozen=True)
class CheckoutEligibilityFailure:
    code: str
    detail: str
    status_code: int
    missing_fields: tuple[str, ...] = ()

    def response_data(self):
        data = {"detail": self.detail, "code": self.code}
        if self.missing_fields:
            data["missing_fields"] = list(self.missing_fields)
        return data


def customer_checkout_eligibility(user):
    """Return (profile, failure); self-declaration is not identity proof.

    The product's current one-time onboarding policy permits checkout after a
    persisted 21+ self-declaration. ``age_verified`` remains a separate,
    stronger document-review state for flows that require it.
    """
    profile = CustomerProfile.objects.filter(user=user).first()
    if profile is None:
        return None, CheckoutEligibilityFailure(
            "profile_incomplete",
            "Complete your personal details before checkout.",
            400,
            ("full_name", "phone", "address_line1", "city", "state", "postal_code"),
        )

    required = {
        "full_name": profile.full_name,
        "phone": profile.phone or user.phone,
        "address_line1": profile.address_line1,
        "city": profile.city,
        "state": profile.state,
        "postal_code": profile.postal_code,
    }
    missing = tuple(name for name, value in required.items() if not str(value or "").strip())
    if missing:
        return profile, CheckoutEligibilityFailure(
            "profile_incomplete",
            "Complete your personal details before checkout.",
            400,
            missing,
        )

    if user.date_of_birth is None:
        return profile, CheckoutEligibilityFailure(
            "date_of_birth_required",
            "Date of birth is required before checkout.",
            403,
        )
    if not is_at_least_minimum_age(user.date_of_birth):
        return profile, CheckoutEligibilityFailure(
            "underage_customer",
            "You must be at least 21 years old to place an order.",
            403,
        )
    # Existing customers with authoritative documentary verification remain
    # eligible; they are not forced back through the newer acknowledgement UI.
    if not is_age_self_declared(user) and not is_age_verified(user):
        return profile, CheckoutEligibilityFailure(
            "age_self_declaration_required",
            "Confirm that you are 21 years or older before checkout.",
            403,
        )
    return profile, None
