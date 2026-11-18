"""Historical phone proofs are retained; editing contact data grants no proof."""
import hashlib
import hmac
import re
from django.conf import settings
from django.utils.crypto import constant_time_compare


def phone_hash(phone):
    return hmac.new(settings.PHONE_PROOF_HMAC_KEY.encode(), phone.encode("ascii"), hashlib.sha256).hexdigest()


def current_phone_verified(user, contact=None):
    identity = getattr(user, "verified_phone_identity", None)
    if identity is None or not getattr(settings, "PHONE_PROOF_HMAC_KEY", ""):
        return False
    raw = re.sub(r"[\s()-]", "", str(user.phone if contact is None else contact))
    if re.fullmatch(r"[6-9][0-9]{9}", raw):
        raw = "+91" + raw
    elif re.fullmatch(r"91[6-9][0-9]{9}", raw):
        raw = "+" + raw
    if not re.fullmatch(r"\+91[6-9][0-9]{9}", raw):
        return False
    return constant_time_compare(identity.phone_hash, phone_hash(raw))
