from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        from . import http_log_privacy, schema  # Register the project's JWT authenticator for OpenAPI.
