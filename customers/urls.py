from django.urls import path

from .views import (
    AccountDeletionRequestCancelView,
    AccountDeletionRequestView,
    CartItemAddView,
    CartItemDeleteView,
    CartItemUpdateView,
    CheckoutPreviewView,
    CustomerCartView,
    CustomerLegalPolicyListView,
    CustomerProfileView,
    CustomerVehicleDetailView,
    CustomerVehicleListCreateView,
    CustomerVerificationSessionDetailView,
    CustomerVerificationSessionListCreateView,
    FavoriteShopDeleteView,
    FavoriteShopListCreateView,
    NotificationListView,
    NotificationReadView,
    CustomerMarkAllNotificationsReadView,
    SupportTicketDetailView,
    SupportTicketListCreateView,
    UserNotificationPreferenceView,
    UserPushTokenView,
)




urlpatterns = [
    path("profile/", CustomerProfileView.as_view(), name="customer-profile"),
    path("vehicles/",CustomerVehicleListCreateView.as_view(),name="customer-vehicle-list-create"),
    path("vehicles/<int:pk>/", CustomerVehicleDetailView.as_view(), name="customer-vehicle-detail"),
    path("favorites/shops/",FavoriteShopListCreateView.as_view(),name="favorite-shop-list-create"),
    path("favorites/shops/<int:pk>/",FavoriteShopDeleteView.as_view(),name="favorite-shop-delete"),
    path("cart/",CustomerCartView.as_view(),name="customer-cart"),
    path("cart/items/",CartItemAddView.as_view(),name="cart-item-add"),
    path("cart/items/<int:pk>/",CartItemUpdateView.as_view(),name="cart-item-update"),
    path("cart/items/<int:pk>/delete/",CartItemDeleteView.as_view(),name="cart-item-delete"),
    path("checkout/preview/",CheckoutPreviewView.as_view(), name="checkout-preview"),
    path("notifications/",NotificationListView.as_view(),name="notification-list",),
path("notifications/<int:pk>/read/", NotificationReadView.as_view(),name="notification-read"),
path("notifications/read-all/", CustomerMarkAllNotificationsReadView.as_view(),name="notification-read-all"),
path("notifications/preferences/", UserNotificationPreferenceView.as_view(),name="notification-preferences"),
path("push-token/", UserPushTokenView.as_view(),name="customer-push-token"),
path("verification/sessions/", CustomerVerificationSessionListCreateView.as_view(),name="customer-verification-sessions"),
path("verification/sessions/<int:pk>/", CustomerVerificationSessionDetailView.as_view(),name="customer-verification-session-detail"),
path("support/tickets/", SupportTicketListCreateView.as_view(),name="support-ticket-list-create"),
path("support/tickets/<int:pk>/", SupportTicketDetailView.as_view(),name="support-ticket-detail"),
path("account/deletion-request/", AccountDeletionRequestView.as_view(),name="account-deletion-request"),
path("account/deletion-request/cancel/", AccountDeletionRequestCancelView.as_view(),name="account-deletion-request-cancel"),
path("legal/policies/", CustomerLegalPolicyListView.as_view(),name="customer-legal-policies"),
    ]