from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class DRINQOJWTAuthenticationScheme(SimpleJWTScheme):
    target_class = "accounts.authentication.JWTAuthentication"
