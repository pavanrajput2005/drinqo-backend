"""Settings import guards are configuration tests, not database migrations."""
import os
import subprocess
import sys
from unittest import TestCase


class SettingsGuardTests(TestCase):
    def check_rejected(self, module, message, **settings):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DRINQO_", "DJANGO_", "MSG91_"))}
        env.update(settings, PYTHON_DOTENV_DISABLED="1")
        result = subprocess.run([sys.executable, "-B", "-c", f"import {module}"], env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)

    def test_isolated_settings_refuse_implicit_use(self):
        self.check_rejected("config.test_settings", "Set DRINQO_TEST_MODE=1")

    def test_production_requires_shared_cache_for_active_auth(self):
        self.check_rejected("config.settings", "shared Django cache", DJANGO_ENVIRONMENT="production",
                            DJANGO_DEBUG="false", DJANGO_SECRET_KEY="synthetic-test-only",
                            SIMPLE_JWT_SIGNING_KEY="test-only-signing-key-with-enough-length",
                            DJANGO_CACHE_BACKEND="django.core.cache.backends.locmem.LocMemCache")
