"""Fail-closed settings for explicitly opted-in, isolated test runs."""

import os
import re
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


if os.environ.get("DRINQO_TEST_MODE") != "1":
    raise ImproperlyConfigured(
        "Set DRINQO_TEST_MODE=1 to use the isolated test settings."
    )

_test_database_variables = {
    "NAME": "DRINQO_TEST_DB_NAME",
    "USER": "DRINQO_TEST_DB_USER",
    "PASSWORD": "DRINQO_TEST_DB_PASSWORD",
    "HOST": "DRINQO_TEST_DB_HOST",
    "PORT": "DRINQO_TEST_DB_PORT",
}
_test_database = {
    key: os.environ.get(variable, "")
    for key, variable in _test_database_variables.items()
}
_test_database["TEST_NAME"] = os.environ.get(
    "DRINQO_TEST_DATABASE_NAME",
    "",
)
if any(not value for value in _test_database.values()):
    raise ImproperlyConfigured(
        "All required DRINQO_TEST_DB_* variables and "
        "DRINQO_TEST_DATABASE_NAME must be explicitly configured."
    )
if (
    _test_database["HOST"] != "127.0.0.1"
    or not re.fullmatch(
        r"drinqo_test_connection_[A-Za-z0-9_]+",
        _test_database["NAME"],
    )
    or not re.fullmatch(
        r"drinqo_test_database_[A-Za-z0-9_]+",
        _test_database["TEST_NAME"],
    )
    or not re.fullmatch(r"drinqo_test_[A-Za-z0-9_]+", _test_database["USER"])
    or _test_database["NAME"] == _test_database["TEST_NAME"]
):
    raise ImproperlyConfigured(
        "Test settings require distinct connection and test database names "
        "with their respective test-only prefixes, a loopback host, and a "
        "test-prefixed role name. These naming checks do not prove isolation."
    )
try:
    _test_database_port = int(_test_database["PORT"])
except ValueError as exc:
    raise ImproperlyConfigured(
        "DRINQO_TEST_DB_PORT must be a valid TCP port."
    ) from exc
if not 1 <= _test_database_port <= 65535:
    raise ImproperlyConfigured(
        "DRINQO_TEST_DB_PORT must be a valid TCP port."
    )

# Populate the base settings' database inputs only from mandatory test values.
os.environ.update(
    {
        "DB_NAME": _test_database["NAME"],
        "DB_USER": _test_database["USER"],
        "DB_PASSWORD": _test_database["PASSWORD"],
        "DB_HOST": _test_database["HOST"],
        "DB_PORT": str(_test_database_port),
        "DJANGO_ENVIRONMENT": "test",
        "DJANGO_DEBUG": "true",
        "DJANGO_SECRET_KEY": "drinqo-isolated-test-secret-key-value",
        "SIMPLE_JWT_SIGNING_KEY": "drinqo-isolated-test-jwt-signing-key",
        "DJANGO_CACHE_BACKEND": (
            "django.core.cache.backends.locmem.LocMemCache"
        ),
        "DJANGO_CACHE_LOCATION": f"drinqo-test-cache-{os.getpid()}",
        "GOOGLE_OAUTH_CLIENT_IDS": "",
        "GOOGLE_OAUTH_ANDROID_CLIENT_IDS": "",
        "PHONE_PROOF_HMAC_KEY": "synthetic-test-only-contact-proof",
        "EMAIL_HOST": "",
        "EMAIL_HOST_USER": "",
        "EMAIL_HOST_PASSWORD": "",
    }
)

from .settings import *  # noqa: E402,F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _test_database["NAME"],
        "USER": _test_database["USER"],
        "PASSWORD": _test_database["PASSWORD"],
        "HOST": _test_database["HOST"],
        "PORT": str(_test_database_port),
        "TEST": {
            "NAME": _test_database["TEST_NAME"],
        },
    }
}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": f"drinqo-test-cache-{os.getpid()}",
    }
}
# Django 6.1's MAILERS setting supersedes the legacy EMAIL_BACKEND setting.
# Keeping mail in memory prevents test code from using any configured SMTP
# service while avoiding Django's mutually-exclusive settings error.
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    }
}
GOOGLE_OAUTH_CLIENT_IDS = ()
GOOGLE_OAUTH_CLIENT_IDS_CONFIG_VALID = False
GOOGLE_OAUTH_ANDROID_CLIENT_IDS = ()
GOOGLE_OAUTH_ANDROID_CLIENT_IDS_CONFIG_VALID = False
MEDIA_ROOT = Path(tempfile.gettempdir()) / (
    f"drinqo-test-media-{os.getpid()}"
)
TEST_RUNNER = "config.test_runner.IsolatedTestRunner"
