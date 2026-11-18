from django.urls import path
from .retired import RetiredPhoneOTPView
from .mobile_views import SetAccountPasswordView

from .views import (
    RegisterView,
    ProfileView,
    AgeVerificationView,
    MeView,
    CustomerTokenObtainPairView,
    CustomerTokenRefreshView,
    LogoutView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    GoogleLoginView,
    GoogleLinkView,
)


urlpatterns = [
    path("set-password/", SetAccountPasswordView.as_view(), name="set-account-password"),
    path("me/", MeView.as_view(), name="me"),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", CustomerTokenObtainPairView.as_view(), name="login"),
    path(
        "token/refresh/",
        CustomerTokenRefreshView.as_view(),
        name="token_refresh",
    ),
    path("logout/", LogoutView.as_view(), name="logout"),
    path(
        "password-reset/",
        PasswordResetRequestView.as_view(),
        name="password-reset",
    ),
    path(
        "password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("age-verification/", AgeVerificationView.as_view(), name="age_verification"),
    path("otp/send/", RetiredPhoneOTPView.as_view(), name="otp-send"),
    path("otp/verify/", RetiredPhoneOTPView.as_view(), name="otp-verify"),
    path("phone-link/request/", RetiredPhoneOTPView.as_view(), name="phone-link-request"),
    path("phone-link/verify/", RetiredPhoneOTPView.as_view(), name="phone-link-verify"),
    path("firebase-login/", RetiredPhoneOTPView.as_view(), name="retired-firebase-login"),
    path("google-login/", GoogleLoginView.as_view(), name="google-login"),
    path("google-link/", GoogleLinkView.as_view(), name="google-link"),
]
