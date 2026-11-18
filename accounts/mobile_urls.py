from django.urls import path
from .mobile_views import EmailSendOTPView, EmailVerifyOTPView, MobileAppleView, MobileGoogleView
from .mobile_views import LinkEmailSendView, LinkEmailVerifyView

urlpatterns = [
    path("link-email/send-otp/", LinkEmailSendView.as_view(), name="link-email-send"),
    path("link-email/verify-otp/", LinkEmailVerifyView.as_view(), name="link-email-verify"),
    path("google/", MobileGoogleView.as_view(), name="mobile-google"),
    path("apple/", MobileAppleView.as_view(), name="mobile-apple"),
    path("send-otp/", EmailSendOTPView.as_view(), name="email-send-otp"),
    path("verify-otp/", EmailVerifyOTPView.as_view(), name="email-verify-otp"),
]
