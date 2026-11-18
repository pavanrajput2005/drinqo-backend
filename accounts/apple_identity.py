"""Apple identity verification; only the fixed Apple JWKS origin is trusted."""
from functools import lru_cache

import jwt
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class InvalidAppleIdentity(Exception):
    pass


class AppleVerificationUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def apple_keys():
    return jwt.PyJWKClient("https://appleid.apple.com/auth/keys", timeout=5)


def verify_apple_identity(raw_token):
    audiences = settings.APPLE_CLIENT_IDS
    if not audiences:
        raise AppleVerificationUnavailable
    try:
        header = jwt.get_unverified_header(raw_token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise InvalidAppleIdentity
        key = apple_keys().get_signing_key_from_jwt(raw_token)
        claims = jwt.decode(
            raw_token, key.key, algorithms=["RS256"],
            audience=list(audiences), issuer="https://appleid.apple.com",
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise InvalidAppleIdentity
        email = claims.get("email")
        if email is not None:
            if not isinstance(email, str) or claims.get("email_verified") not in (True, "true"):
                raise InvalidAppleIdentity
            email = email.strip().lower()
            validate_email(email)
        return subject, email
    except jwt.PyJWKClientConnectionError:
        raise AppleVerificationUnavailable from None
    except (jwt.PyJWTError, ValidationError, ValueError, TypeError, KeyError):
        raise InvalidAppleIdentity from None
