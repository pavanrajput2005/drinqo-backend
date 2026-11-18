from django.urls import path

from . import super_admin_views as views


urlpatterns = [
    path("me/", views.super_admin_me, name="super-admin-me"),
    path("dashboard/", views.super_admin_dashboard, name="super-admin-dashboard"),
    path("notifications/", views.super_admin_notifications, name="super-admin-notifications"),
    path(
        "notifications/<int:notification_id>/read/",
        views.super_admin_mark_notification_read,
        name="super-admin-notification-read",
    ),
    path("payments/", views.super_admin_payments, name="super-admin-payments"),
    path("payments/cases/", views.super_admin_payment_cases, name="super-admin-payment-cases"),
    path("payments/<uuid:payment_id>/cases/", views.super_admin_create_payment_case, name="super-admin-payment-case-create"),
    path("payments/<uuid:payment_id>/", views.super_admin_payment_detail, name="super-admin-payment-detail"),
    path("payments/<uuid:payment_id>/refund/retry/", views.super_admin_retry_payment_refund, name="super-admin-payment-refund-retry"),
    path(
        "settings/",
        views.super_admin_platform_settings,
        name="super-admin-settings",
    ),
    path(
        "verification-sessions/",
        views.super_admin_verification_sessions,
        name="super-admin-verification-sessions",
    ),
    path(
        "verification-sessions/<int:session_id>/document/",
        views.super_admin_verification_document,
        name="super-admin-verification-document",
    ),
    path(
        "support-tickets/",
        views.super_admin_support_tickets,
        name="super-admin-support-tickets",
    ),
    path(
        "support-tickets/<int:ticket_id>/",
        views.super_admin_support_ticket_detail,
        name="super-admin-support-ticket-detail",
    ),
    path(
        "legal-policies/",
        views.super_admin_legal_policies,
        name="super-admin-legal-policies",
    ),
    path(
        "legal-policies/<int:policy_id>/",
        views.super_admin_legal_policy_detail,
        name="super-admin-legal-policy-detail",
    ),
    path(
        "shop-applications/",
        views.super_admin_shop_applications,
        name="super-admin-shop-applications",
    ),
    path(
        "shop-applications/<int:application_id>/",
        views.super_admin_shop_application_detail,
        name="super-admin-shop-application-detail",
    ),
    path(
        "shop-applications/<int:application_id>/approve/",
        views.super_admin_approve_shop_application,
        name="super-admin-shop-application-approve",
    ),
    path(
        "shop-applications/<int:application_id>/reject/",
        views.super_admin_reject_shop_application,
        name="super-admin-shop-application-reject",
    ),
    path(
        "shop-applications/<int:application_id>/request-changes/",
        views.super_admin_request_shop_application_changes,
        name="super-admin-shop-application-request-changes",
    ),
    path(
        "license-documents/pending/",
        views.super_admin_pending_license_documents,
        name="super-admin-pending-license-documents",
    ),
    path("location-requests/", views.super_admin_location_requests, name="super-admin-location-requests"),
    path("location-requests/<int:request_id>/review/", views.super_admin_review_location_request, name="super-admin-location-request-review"),
    path("shops/", views.super_admin_shops, name="super-admin-shops"),
    path(
        "shops/<int:shop_id>/",
        views.super_admin_shop_detail,
        name="super-admin-shop-detail",
    ),
    path(
        "shops/<int:shop_id>/status/",
        views.super_admin_shop_status,
        name="super-admin-shop-status",
    ),
    path(
        "shops/<int:shop_id>/license-documents/<int:document_id>/review/",
        views.super_admin_review_shop_license_document,
        name="super-admin-shop-license-review",
    ),
    path("customers/", views.super_admin_customers, name="super-admin-customers"),
    path(
        "customers/<int:customer_id>/",
        views.super_admin_customer_detail,
        name="super-admin-customer-detail",
    ),
    path(
        "customers/<int:customer_id>/status/",
        views.super_admin_customer_status,
        name="super-admin-customer-status",
    ),
    path(
        "orders/pending-approval/",
        views.super_admin_pending_orders,
        name="super-admin-pending-orders",
    ),
    path(
        "orders/restricted-shop/",
        views.super_admin_restricted_shop_orders,
        name="super-admin-restricted-shop-orders",
    ),
    path(
        "orders/<int:order_id>/shop-review/",
        views.super_admin_review_restricted_shop_order,
        name="super-admin-restricted-shop-order-review",
    ),
    path(
        "orders/<int:order_id>/approval/",
        views.super_admin_order_approval,
        name="super-admin-order-approval",
    ),
    path(
        "high-value-orders/",
        views.super_admin_high_value_orders,
        name="super-admin-high-value-orders",
    ),
    path(
        "high-value-orders/<int:order_id>/",
        views.super_admin_high_value_order_detail,
        name="super-admin-high-value-order-detail",
    ),
    path(
        "high-value-orders/<int:order_id>/approve/",
        views.super_admin_approve_high_value_order,
        name="super-admin-high-value-order-approve",
    ),
    path(
        "high-value-orders/<int:order_id>/reject/",
        views.super_admin_reject_high_value_order,
        name="super-admin-high-value-order-reject",
    ),
    path("reports/", views.super_admin_reports, name="super-admin-reports"),
    path(
        "audit-logs/",
        views.super_admin_audit_logs,
        name="super-admin-audit-logs",
    ),
]
