from django.urls import path
from .staff_handover_views import RetiredHandoverOTPView, CustomerHandoverLocationView

from .payment_views import PaymentInitiateView, PaymentReconcileView, PaymentVerifyView, RazorpayWebhookView

from .views import (
    OrderCreateView,
    OrderHistoryView,
    OrderDetailView,
    OrderCancelView,
    PaymentStatusView,
    OrderTrackingView,
    OrderLiveLocationView,
)

urlpatterns = [
    path("payments/razorpay/webhook/", RazorpayWebhookView.as_view(), name="razorpay-webhook"),
    path("<int:pk>/payment/initiate/", PaymentInitiateView.as_view(), name="payment-initiate"),
    path("<int:pk>/payment/verify/", PaymentVerifyView.as_view(), name="payment-verify"),
    path("<int:pk>/payment/reconcile/", PaymentReconcileView.as_view(), name="payment-reconcile"),
    path("<int:pk>/handover-otp/", RetiredHandoverOTPView.as_view(), name="order-handover-otp"),
    path("<int:pk>/handover-otp/reissue/", RetiredHandoverOTPView.as_view(), name="order-handover-otp-reissue"),
    path("<int:pk>/handover-location/", CustomerHandoverLocationView.as_view(), name="customer-handover-location"),
    path(
        "create/",
        OrderCreateView.as_view(),
        name="order-create",
    ),

    path(
        "history/",
        OrderHistoryView.as_view(),
        name="order-history",
    ),

    path(
        "<int:pk>/payment-status/",
        PaymentStatusView.as_view(),
        name="payment-status",
    ),

    path(
        "<int:pk>/cancel/",
        OrderCancelView.as_view(),
        name="order-cancel",
    ),

    path(
        "<int:pk>/",
        OrderDetailView.as_view(),
        name="order-detail",
    ),

    path(
    "<int:pk>/tracking/",
    OrderTrackingView.as_view(),
    name="order-tracking",
),
path(
    "<int:pk>/live-location/",
    OrderLiveLocationView.as_view(),
    name="order-live-location",
),
path(
    "<int:pk>/verify-pickup/",
    RetiredHandoverOTPView.as_view(),
    name="verify-pickup",
),
]
