"""Test runner that blocks HTTP requests and cleans its temporary media."""

import tempfile
from unittest.mock import patch

from django.test import override_settings
from django.test.runner import DiscoverRunner


class IsolatedTestRunner(DiscoverRunner):
    def __init__(self, *args, **kwargs):
        if kwargs.get("parallel", 0):
            raise ValueError(
                "Parallel workers are disabled in the isolated test runner."
            )
        super().__init__(*args, **kwargs)

    def run_tests(self, test_labels, **kwargs):
        with patch(
            "requests.sessions.Session.request",
            side_effect=RuntimeError(
                "HTTP requests are disabled in the isolated test environment. "
                "This does not block other network transports, raw sockets, "
                "or child processes."
            ),
        ):
            try:
                return super().run_tests(test_labels, **kwargs)
            finally:
                if getattr(self, "_test_environment_active", False):
                    self.teardown_test_environment()
                else:
                    self._cleanup_test_media()

    def setup_test_environment(self, **kwargs):
        self._test_media_directory = tempfile.TemporaryDirectory(
            prefix="drinqo-django-test-media-"
        )
        self._test_media_override = override_settings(
            MEDIA_ROOT=self._test_media_directory.name
        )
        self._test_media_override.enable()
        try:
            super().setup_test_environment(**kwargs)
            self._test_environment_active = True
        except Exception:
            self._cleanup_test_media()
            raise

    def teardown_test_environment(self, **kwargs):
        try:
            super().teardown_test_environment(**kwargs)
        finally:
            self._test_environment_active = False
            self._cleanup_test_media()

    def _cleanup_test_media(self):
        media_override = getattr(self, "_test_media_override", None)
        if media_override is not None:
            self._test_media_override = None
            media_override.disable()

        media_directory = getattr(self, "_test_media_directory", None)
        if media_directory is not None:
            self._test_media_directory = None
            media_directory.cleanup()
