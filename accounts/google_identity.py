"""Server-side verification for Google OpenID Connect ID tokens."""

from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token


GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
GOOGLE_CERTS_TIMEOUT_SECONDS = 5
GOOGLE_LINK_TOKEN_MAX_AGE_SECONDS = 5 * 60
GOOGLE_IAT_CLOCK_SKEW_SECONDS = 60


class InvalidGoogleIdentity(Exception):
    """The submitted ID token or its identity claims are invalid."""

    def __init__(self, reason_code="invalid_identity"):
        super().__init__(reason_code)
        self.reason_code = reason_code


class GoogleVerificationUnavailable(Exception):
    """Google verification configuration or infrastructure is unavailable."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    first_name: str = ""
    last_name: str = ""


class _TimeoutGoogleRequest(GoogleRequest):
    """Bound certificate retrieval so a Google outage cannot hang a request."""

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        return super().__call__(
            url,
            method=method,
            body=body,
            headers=headers,
            timeout=GOOGLE_CERTS_TIMEOUT_SECONDS,
            **kwargs,
        )


def _audience_is_allowed(
    claims,
    allowed_client_ids,
    allowed_authorized_party_ids=(),
):
    audience = claims.get("aud")
    authorized_party = claims.get("azp")
    allowed_authorized_parties = (
        set(allowed_client_ids) | set(allowed_authorized_party_ids)
    )

    if isinstance(audience, str):
        if audience not in allowed_client_ids:
            return "audience_mismatch"
    elif (
        isinstance(audience, list)
        and audience
        and all(isinstance(item, str) and item for item in audience)
        and set(audience).issubset(allowed_client_ids)
    ):
        if len(set(audience)) > 1 and not authorized_party:
            return "authorized_party_missing"
    else:
        return "audience_mismatch"

    if authorized_party is not None and (
        not isinstance(authorized_party, str)
        or authorized_party not in allowed_authorized_parties
    ):
        return "authorized_party_mismatch"
    return None


def _reject_identity(reason_code):
    raise InvalidGoogleIdentity(reason_code) from None


def verify_google_id_token(raw_token, require_fresh=False):
    """Verify a Google ID token and return only its trusted subject and email."""
    if (
        not getattr(settings, "GOOGLE_OAUTH_CLIENT_IDS_CONFIG_VALID", False)
        or not getattr(settings, "GOOGLE_OAUTH_CLIENT_IDS", ())
    ):
        raise GoogleVerificationUnavailable from None

    if not isinstance(raw_token, str) or not raw_token or len(raw_token) > 8192:
        _reject_identity("invalid_token_format")

    try:
        # Signature, issuer, and expiry are verified by google-auth. Audience is
        # checked below against the configured allowlist to support several app IDs.
        claims = google_id_token.verify_oauth2_token(
            raw_token,
            _TimeoutGoogleRequest(),
            audience=None,
        )
    except google_auth_exceptions.TransportError:
        raise GoogleVerificationUnavailable from None
    except google_auth_exceptions.MalformedError:
        _reject_identity("token_signature_or_format_invalid")
    except google_auth_exceptions.InvalidValue:
        _reject_identity("token_expiry_or_time_invalid")
    except (ValueError, google_auth_exceptions.GoogleAuthError):
        _reject_identity("google_token_verification_rejected")
    except Exception:
        # Fail closed for unexpected verifier/transport failures and never echo
        # an exception that might contain token or request details.
        raise GoogleVerificationUnavailable from None

    if not isinstance(claims, Mapping):
        _reject_identity("invalid_claims")

    allowed_client_ids = set(settings.GOOGLE_OAUTH_CLIENT_IDS)
    issuer = claims.get("iss")
    if not isinstance(issuer, str) or issuer not in GOOGLE_ISSUERS:
        _reject_identity("issuer_mismatch")

    authorized_party_ids = ()
    if getattr(
        settings,
        "GOOGLE_OAUTH_ANDROID_CLIENT_IDS_CONFIG_VALID",
        False,
    ):
        authorized_party_ids = getattr(
            settings,
            "GOOGLE_OAUTH_ANDROID_CLIENT_IDS",
            (),
        )
    audience_error = _audience_is_allowed(
        claims,
        allowed_client_ids,
        authorized_party_ids,
    )
    if audience_error:
        _reject_identity(audience_error)

    subject = claims.get("sub")
    email = claims.get("email")
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > 255
        or not subject.isascii()
    ):
        _reject_identity("invalid_subject")
    if not isinstance(email, str) or not email.strip():
        _reject_identity("invalid_email")
    if claims.get("email_verified") is not True:
        _reject_identity("email_not_verified")

    if require_fresh:
        issued_at = claims.get("iat")
        now = timezone.now().timestamp()
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, (int, float))
            or issued_at < now - GOOGLE_LINK_TOKEN_MAX_AGE_SECONDS
            or issued_at > now + GOOGLE_IAT_CLOCK_SKEW_SECONDS
        ):
            _reject_identity("link_token_not_fresh")

    email = email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        _reject_identity("invalid_email")

    return GoogleIdentity(
        subject=subject, email=email,
        first_name=claims.get("given_name", "")[:150] if isinstance(claims.get("given_name", ""), str) else "",
        last_name=claims.get("family_name", "")[:150] if isinstance(claims.get("family_name", ""), str) else "",
    )
