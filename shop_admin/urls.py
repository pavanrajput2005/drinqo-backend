
from django.urls import path
from orders.staff_handover_views import StaffConfirmHandoverView, RetiredHandoverOTPView

from . import api_views
from . import location_views
from .views import (
    ShopLicenseDocumentDetailView,
    ShopLicenseDocumentDownloadView,
    ShopApplicationDocumentDownloadView,
    ShopApplicationView,
    ShopLicenseDocumentUploadView,
    ShopAdminProfileView,
    ShopOnboardingView,
    ShopApplicationDocumentDetailView,
    ShopLicenseDocumentListCreateView,
)

urlpatterns = [
    path("orders/<int:order_id>/confirm-handover/", StaffConfirmHandoverView.as_view(), name="staff-confirm-handover"),
    # Profile and shop
    path(
        "profile/",
        ShopAdminProfileView.as_view(),
        name="shop_admin_profile",
    ),
    path(
        "licenses/",
        ShopLicenseDocumentListCreateView.as_view(),
        name="shop_license_list_create",
    ),
    path(
        "licenses/<int:document_id>/",
        ShopLicenseDocumentDetailView.as_view(),
        name="shop_license_detail",
    ),
    path(
        "licenses/<int:document_id>/download/",
        ShopLicenseDocumentDownloadView.as_view(),
        name="shop_license_download",
    ),
    path(
        "application/",
        ShopApplicationView.as_view(),
        name="shop-admin-application",
    ),
    path(
        "application/documents/",
        ShopLicenseDocumentUploadView.as_view(),
        name="shop-admin-license-document-upload",
    ),
    path(
        "onboarding/",
        ShopOnboardingView.as_view(),
        name="shop-admin-onboarding",
    ),
    path(
        "application/documents/<int:document_id>/download/",
        ShopApplicationDocumentDownloadView.as_view(),
        name="shop-application-document-download",
    ),
    path(
        "application/documents/<int:document_id>/",
        ShopApplicationDocumentDetailView.as_view(),
        name="shop-application-document-detail",
    ),
    path(
        "me/",
        api_views.shop_admin_me,
        name="shop_admin_me",
    ),
    path(
        "shop/",
        api_views.shop_admin_update_shop,
        name="shop_admin_update_shop",
    ),
    path(
        "shop/availability/",
        api_views.shop_admin_update_shop_availability,
        name="shop_admin_update_shop_availability",
    ),
    path("location/", location_views.shop_admin_location, name="shop-admin-location"),
    path("location/requests/", location_views.shop_admin_location_requests, name="shop-admin-location-requests"),
    path(
        "inventory/",
        api_views.shop_admin_inventory,
        name="shop_admin_inventory",
    ),
    path(
        "inventory/<int:product_id>/adjust/",
        api_views.shop_admin_adjust_inventory,
        name="shop_admin_adjust_inventory",
    ),

    # Dashboard
    path(
        "dashboard/",
        api_views.shop_admin_dashboard,
        name="shop_admin_dashboard",
    ),

    # Notifications
    path(
        "notifications/",
        api_views.shop_admin_notifications,
        name="shop_admin_notifications",
    ),
    path(
        "notifications/<int:notification_id>/read/",
        api_views.shop_admin_mark_notification_read,
        name="shop_admin_mark_notification_read",
    ),
    path(
        "notifications/read-all/",
        api_views.shop_admin_mark_all_notifications_read,
        name="shop_admin_mark_all_notifications_read",
    ),
    path(
        "notifications/preferences/",
        api_views.shop_admin_notification_preferences,
        name="shop_admin_notification_preferences",
    ),
    path(
        "push-token/",
        api_views.shop_admin_push_token,
        name="shop_admin_push_token",
    ),

    # Products
    path(
        "products/",
        api_views.shop_admin_products,
        name="shop_admin_products",
    ),
    path(
        "products/<int:product_id>/",
        api_views.shop_admin_product_detail,
        name="shop_admin_product_detail",
    ),
    path(
        "products/<int:product_id>/image/",
        api_views.shop_admin_product_image,
        name="shop_admin_product_image",
    ),
    path(
        "products/bulk-import/",
        api_views.shop_admin_bulk_import_products,
        name="shop_admin_bulk_import_products",
    ),
    path(
        "reports/",
        api_views.shop_admin_reports,
        name="shop_admin_reports",
    ),

    # Orders
    path(
        "orders/",
        api_views.shop_admin_orders,
        name="shop_admin_orders",
    ),
    path(
        "orders/<int:order_id>/",
        api_views.shop_admin_order_detail,
        name="shop_admin_order_detail",
    ),
    path(
        "orders/<int:order_id>/decision/",
        api_views.shop_admin_order_decision,
        name="shop-admin-order-decision",
    ),
    path(
        "orders/<int:order_id>/accept/",
        api_views.shop_admin_accept_order,
        name="shop_admin_accept_order",
    ),
    path(
        "orders/<int:order_id>/reject/",
        api_views.shop_admin_reject_order,
        name="shop_admin_reject_order",
    ),
    path(
        "orders/<int:order_id>/complete/",
        RetiredHandoverOTPView.as_view(),
        name="shop_admin_complete_order",
    ),
    path(
        "orders/<int:order_id>/pending/",
        api_views.shop_admin_keep_order_pending,
        name="shop_admin_keep_order_pending",
    ),
        path(
        "orders/<int:order_id>/status/",
        api_views.shop_admin_update_order_status,
        name="shop_admin_update_order_status",
    ),
]
