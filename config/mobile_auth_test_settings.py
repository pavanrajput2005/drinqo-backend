"""Standalone, synthetic SQLite settings. Never imports settings.py or .env.

Only for auth regression tests; not evidence of PostgreSQL lock concurrency.
Run: python manage.py test accounts.test_mobile_auth --settings=config.mobile_auth_test_settings
"""
from datetime import timedelta

SECRET_KEY = "synthetic-mobile-auth-test-key-not-for-deployment"
EMAIL_OTP_HMAC_KEY = "independent-synthetic-email-otp-test-key-only"
PHONE_PROOF_HMAC_KEY = "retained-synthetic-historical-phone-hash-key"
DEBUG = False
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
ROOT_URLCONF = "config.urls"
ALLOWED_HOSTS = ["testserver"]
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.admin", "django.contrib.staticfiles",
    "rest_framework", "rest_framework_simplejwt.token_blacklist", "drf_spectacular",
    "accounts", "customers", "shops", "products", "orders", "shop_admin",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "django.template.context_processors.request",
    ]},
}]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
DEFAULT_FROM_EMAIL = "test@example.test"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
APPLE_CLIENT_IDS = ("test.drinqo.app",)
GOOGLE_OAUTH_CLIENT_IDS = ("test-client",)
GOOGLE_OAUTH_CLIENT_IDS_CONFIG_VALID = True
GOOGLE_OAUTH_ANDROID_CLIENT_IDS_CONFIG_VALID = True
GOOGLE_OAUTH_ANDROID_CLIENT_IDS = ()
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["accounts.authentication.JWTAuthentication"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "google_login": "1000/minute", "google_link": "1000/minute",
        "apple_login": "1000/minute", "email_otp_send": "1000/minute",
        "email_otp_verify": "1000/minute", "mobile_profile": "1000/minute",
        "password_login": "1000/minute", "password_reset_request": "1000/minute",
        "password_reset_confirm": "1000/minute",
    },
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=5),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "SIGNING_KEY": SECRET_KEY,
}
