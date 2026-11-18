from decimal import Decimal
from datetime import date, datetime, timedelta, timezone as datetime_timezone
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import (
    CustomerProfile, CustomerVehicle,
    CustomerVerificationSession,
    LegalPolicy,
    Notification,
    SupportTicket,
)
from orders.models import Order, OrderItem
from orders.testing_handover import HANDOVER_TEST_LOCATION, staff_confirmation_payload
from orders.testing_payments import record_captured_payment_for_test
from products.models import Product
from shops.models import Shop, ShopLicenseDocument
from shop_admin.models import PlatformSettings, SuperAdminAuditLog
from rest_framework_simplejwt.tokens import RefreshToken


class SuperAdminApiTests(TestCase):
    def setUp(self):
        media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(media_dir.cleanup)
        settings_override = override_settings(MEDIA_ROOT=media_dir.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="superadmin",
            email="superadmin@example.com",
            password="test-password",
            user_type="super_admin",
        )
        self.shop_admin = User.objects.create_user(
            username="shopadmin",
            email="shopadmin@example.com",
            password="test-password",
            user_type="shop_admin",
        )
        self.customer = User.objects.create_user(
            username="customer",
            email="customer@example.com",
            password="test-password",
            user_type="customer",
            phone="+919876543210",
            age_verified=True,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Test Shop",
            **HANDOVER_TEST_LOCATION,
            phone="9876543210",
            email="shop@example.com",
            address_line1="1 Test Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="TEST-LICENSE-1",
            license_status="pending",
            is_active=False,
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/super-admin-verified-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/super-admin-pending-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="pending",
        )
        self.product = Product.objects.create(
            shop=self.shop,
            name="Test Product",
            category="other",
            price=Decimal("15000.00"),
            stock_quantity=8,
        )
        self.order = Order.objects.create(
            customer=self.customer,
            shop=self.shop,
            status="pending_super_admin",
            subtotal=Decimal("15000.00"),
            application_fee=Decimal("50.00"),
            total_amount=Decimal("15050.00"),
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            unit_price=Decimal("15000.00"),
            quantity=2,
            subtotal=Decimal("15000.00"),
        )
        self.client.force_authenticate(self.admin)

    def test_super_admin_endpoint_rejects_invalid_jwt(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(
            reverse("super-admin-me"),
            HTTP_AUTHORIZATION="Bearer not-a-django-jwt",
        )
        self.assertEqual(response.status_code, 401)

    def test_non_super_admin_is_forbidden(self):
        self.client.force_authenticate(self.shop_admin)
        response = self.client.get(reverse("super-admin-me"))
        self.assertEqual(response.status_code, 403)

    def test_inactive_super_admin_token_is_rejected(self):
        access = str(RefreshToken.for_user(self.admin).access_token)
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        self.client.force_authenticate(user=None)
        response = self.client.get(
            reverse("super-admin-me"),
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(response.status_code, 401)

    def test_super_admin_me_does_not_expose_firebase_identity(self):
        response = self.client.get(reverse("super-admin-me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user_type"], "super_admin")
        self.assertNotIn("firebase_uid", response.data)

    def test_shop_application_approval_is_audited_and_duplicate_is_blocked(self):
        approve_url = reverse(
            "super-admin-shop-application-approve",
            args=[self.shop.id],
        )
        response = self.client.post(approve_url, {"note": "License checked."}, format="json")
        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "verified")
        self.assertTrue(self.shop.is_active)
        self.assertEqual(
            SuperAdminAuditLog.objects.filter(
                action="shop_application_approved",
                resource_id=self.shop.id,
            ).count(),
            1,
        )

        duplicate_response = self.client.post(approve_url, {}, format="json")
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertEqual(
            SuperAdminAuditLog.objects.filter(
                action="shop_application_approved",
                resource_id=self.shop.id,
            ).count(),
            1,
        )

    def test_shop_application_approval_requires_pending_license_document(self):
        self.shop.application_documents.filter(
            verification_status="pending"
        ).delete()
        response = self.client.post(
            reverse("super-admin-shop-application-approve", args=[self.shop.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "pending")
        self.assertFalse(self.shop.is_active)

    def test_super_admin_can_verify_pending_license_renewal(self):
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.latitude = "0.000000"
        self.shop.longitude = "0.050000"
        self.shop.save(
            update_fields=[
                "license_status",
                "is_active",
                "latitude",
                "longitude",
            ]
        )
        self.shop.application_documents.filter(
            verification_status="verified"
        ).delete()
        document = ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number="LICENSE-RENEWAL-VERIFICATION",
            document="private_shop_licenses/license-renewal-review.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="pending",
        )
        customer = APIClient()
        customer.force_authenticate(self.customer)
        PlatformSettings.objects.create(nearby_shop_radius_km="10.00")
        list_url = reverse("customer-shop-list")
        nearby_url = reverse("nearby-shops")
        self.assertFalse(
            any(
                item["id"] == self.shop.id
                for item in customer.get(list_url).data["results"]
            )
        )
        self.assertEqual(
            customer.get(
                nearby_url,
                {"latitude": "0", "longitude": "0"},
            ).data["count"],
            0,
        )

        response = self.client.post(
            reverse(
                "super-admin-shop-license-review",
                args=[self.shop.id, document.id],
            ),
            {"decision": "verify"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.shop.refresh_from_db()
        self.assertEqual(document.verification_status, "verified")
        self.assertIsNotNone(document.verified_at)
        self.assertEqual(self.shop.license_status, "verified")
        self.assertEqual(self.shop.license_number, document.license_number)
        self.assertTrue(self.shop.is_active)
        self.assertTrue(
            SuperAdminAuditLog.objects.filter(
                action="shop_license_document_verified",
                resource_id=document.id,
            ).exists()
        )
        listed_shop = next(
            item
            for item in customer.get(list_url).data["results"]
            if item["id"] == self.shop.id
        )
        nearby_shop = next(
            item
            for item in customer.get(
                nearby_url,
                {"latitude": "0", "longitude": "0"},
            ).data["shops"]
            if item["id"] == self.shop.id
        )
        self.assertEqual(listed_shop["latitude"], "0.000000")
        self.assertEqual(listed_shop["longitude"], "0.050000")
        self.assertEqual(nearby_shop["latitude"], "0.000000")
        self.assertEqual(nearby_shop["longitude"], "0.050000")

    def test_non_super_admin_cannot_review_license_document(self):
        document = ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number="LICENSE-RENEWAL-FORBIDDEN",
            document="private_shop_licenses/license-renewal-forbidden.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="pending",
        )
        self.client.force_authenticate(self.customer)

        response = self.client.post(
            reverse(
                "super-admin-shop-license-review",
                args=[self.shop.id, document.id],
            ),
            {"decision": "verify"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertEqual(document.verification_status, "pending")

    def test_shop_application_rejection_records_reason(self):
        response = self.client.post(
            reverse(
                "super-admin-shop-application-reject",
                args=[self.shop.id],
            ),
            {"reason": "License document is invalid."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        audit = SuperAdminAuditLog.objects.get(
            action="shop_application_rejected",
            resource_id=self.shop.id,
        )
        self.assertEqual(audit.metadata["reason"], "License document is invalid.")

    def test_shop_application_submission_rejection_resubmission_and_approval(self):
        applicant = User.objects.create_user(
            username="new-shop-admin",
            email="new-shop-admin@example.com",
            password="test-password",
            user_type="shop_admin",
        )
        self.client.force_authenticate(applicant)
        application_url = reverse("shop-admin-application")
        submission = {
            "shop_name": "New Applicant Shop",
            "phone": "9876501234",
            "email": "new-shop@example.com",
            "address_line1": "10 Market Road",
            "city": "Pune",
            "state": "Maharashtra",
            "postal_code": "411002",
            "license_number": "NEW-APPLICANT-LICENSE",
            "expiry_date": "2030-12-31",
            "license_document": SimpleUploadedFile(
                "license.pdf",
                b"%PDF-1.4 test license content",
                content_type="application/pdf",
            ),
        }
        submitted = self.client.post(
            application_url,
            submission,
            format="multipart",
        )
        self.assertEqual(submitted.status_code, 201)
        shop_id = submitted.data["id"]
        self.assertEqual(submitted.data["application_status"], "pending")
        self.assertFalse(submitted.data["is_active"])

        self.client.force_authenticate(self.admin)
        detail = self.client.get(
            reverse("super-admin-shop-application-detail", args=[shop_id])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.data["documents"]), 1)
        document_download = reverse(
            "shop-application-document-download",
            args=[detail.data["documents"][0]["id"]],
        )
        self.client.force_authenticate(self.customer)
        forbidden_download = self.client.get(document_download)
        self.assertEqual(forbidden_download.status_code, 403)

        self.client.force_authenticate(self.admin)
        rejected = self.client.post(
            reverse("super-admin-shop-application-reject", args=[shop_id]),
            {"reason": "Please upload a clearer license."},
            format="json",
        )
        self.assertEqual(rejected.status_code, 200)

        self.client.force_authenticate(applicant)
        resubmission = {
            **submission,
            "license_number": "NEW-APPLICANT-LICENSE-2",
            "license_document": SimpleUploadedFile(
                "license-revised.pdf",
                b"%PDF-1.4 revised license content",
                content_type="application/pdf",
            ),
        }
        resubmitted = self.client.post(
            application_url,
            resubmission,
            format="multipart",
        )
        self.assertEqual(resubmitted.status_code, 201)
        self.assertEqual(resubmitted.data["application_status"], "pending")
        self.assertEqual(len(resubmitted.data["documents"]), 2)
        self.assertEqual(
            resubmitted.data["documents"][1]["verification_status"],
            "rejected",
        )

        self.client.force_authenticate(self.admin)
        approved = self.client.post(
            reverse("super-admin-shop-application-approve", args=[shop_id]),
            {"note": "Revised license checked."},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)
        shop = Shop.objects.get(pk=shop_id)
        self.assertEqual(shop.license_status, "verified")
        self.assertTrue(shop.is_active)
        self.assertEqual(
            shop.application_documents.get(
                verification_status="verified"
            ).license_number,
            "NEW-APPLICANT-LICENSE-2",
        )

        self.client.force_authenticate(applicant)
        product_response = self.client.post(
            reverse("shop_admin_products"),
            {
                "name": "Approved Shop Product",
                "category": "other",
                "price": "500.00",
                "stock_quantity": 2,
            },
            format="json",
        )
        self.assertEqual(product_response.status_code, 201)

    def test_shop_onboarding_uses_authenticated_customer_without_linking_identity(self):
        applicant = self.customer
        self.client.force_authenticate(applicant)
        submission = {
            "shop_name": "Authenticated Onboarding Shop",
            "phone": "9876501234",
            "email": "onboarding-shop@example.com",
            "address_line1": "20 Onboarding Road",
            "city": "Pune",
            "state": "Maharashtra",
            "postal_code": "411003",
            "license_number": "AUTHENTICATED-ONBOARDING-LICENSE",
            "expiry_date": "2030-12-31",
            "license_document": SimpleUploadedFile(
                "onboarding-license.pdf",
                b"%PDF-1.4 onboarding license",
                content_type="application/pdf",
            ),
        }

        response = self.client.post(
            reverse("shop-admin-onboarding"),
            submission,
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["application_status"], "pending")
        self.assertFalse(response.data["is_active"])
        applicant.refresh_from_db()
        self.assertEqual(applicant.user_type, "shop_admin")
        self.assertIsNone(applicant.firebase_uid)
        shop = Shop.objects.get(owner=applicant)
        self.assertFalse(shop.is_active)
        self.assertEqual(ShopLicenseDocument.objects.filter(shop=shop).count(), 1)
        self.assertTrue(
            SuperAdminAuditLog.objects.filter(
                actor=applicant,
                action="shop_application_submitted",
                resource_id=shop.id,
            ).exists()
        )

        duplicate_submission = {
            **submission,
            "license_document": SimpleUploadedFile(
                "duplicate-license.pdf",
                b"%PDF-1.4 duplicate license",
                content_type="application/pdf",
            ),
        }
        duplicate_response = self.client.post(
            reverse("shop-admin-onboarding"),
            duplicate_submission,
            format="multipart",
        )
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertEqual(ShopLicenseDocument.objects.filter(shop=shop).count(), 1)

        self.client.force_authenticate(self.admin)
        applications = self.client.get(
            reverse("super-admin-shop-applications"),
            {"status": "pending"},
        )
        self.assertEqual(applications.status_code, 200)
        self.assertIn(
            shop.id,
            [item["id"] for item in applications.data["results"]],
        )

    def test_shop_onboarding_rejects_non_jwt_bearer_token(self):
        self.client.force_authenticate(user=None)
        user_count = User.objects.count()
        response = self.client.post(
            reverse("shop-admin-onboarding"),
            {
                "shop_name": "Unauthenticated Shop",
                "license_document": SimpleUploadedFile(
                    "license.pdf",
                    b"%PDF-1.4 unauthenticated request",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
            HTTP_AUTHORIZATION="Bearer not-a-jwt",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), user_count)

    def test_shop_onboarding_does_not_link_existing_customer_by_email(self):
        applicant = User.objects.create_user(
            username="existing-onboarding-customer",
            email="existing-onboarding@example.com",
            password="StrongTestPassword#123",
            user_type="customer",
        )
        self.client.force_authenticate(applicant)
        submission = {
            "shop_name": "Linked Account Shop",
            "phone": "9876509876",
            "address_line1": "30 Linked Account Road",
            "city": "Pune",
            "state": "Maharashtra",
            "postal_code": "411004",
            "license_number": "LINKED-ONBOARDING-LICENSE",
            "expiry_date": "2030-12-31",
            "license_document": SimpleUploadedFile(
                "linked-license.pdf",
                b"%PDF-1.4 linked license",
                content_type="application/pdf",
            ),
        }

        response = self.client.post(
            reverse("shop-admin-onboarding"),
            submission,
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        applicant.refresh_from_db()
        self.assertIsNone(applicant.firebase_uid)
        self.assertEqual(applicant.user_type, "shop_admin")
        self.assertFalse(Shop.objects.get(owner=applicant).is_active)
    def test_customer_block_and_unblock_are_audited_and_block_login(self):
        customer_access = str(RefreshToken.for_user(self.customer).access_token)
        status_url = reverse(
            "super-admin-customer-status",
            args=[self.customer.id],
        )
        response = self.client.patch(
            status_url,
            {"is_blocked": True, "reason": "Policy violation."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_blocked)

        self.client.force_authenticate(user=None)
        response = self.client.get(
            reverse("super-admin-me"),
            HTTP_AUTHORIZATION=f"Bearer {customer_access}",
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            status_url,
            {"is_blocked": False, "reason": "Review complete."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_blocked)
        self.assertEqual(
            SuperAdminAuditLog.objects.filter(
                resource_type="customer",
                resource_id=self.customer.id,
            ).count(),
            2,
        )

    def test_blocked_customer_cannot_use_password_login(self):
        self.customer.set_password("test-password")
        self.customer.is_blocked = True
        self.customer.save(update_fields=["password", "is_blocked"])
        client = APIClient()
        response = client.post(
            reverse("login"),
            {"email": self.customer.email, "password": "test-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        response = client.post(
            reverse("token_refresh"),
            {"refresh": str(RefreshToken.for_user(self.customer))},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
    def test_shop_suspension_is_audited_and_unknown_shop_is_not_found(self):
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.save(update_fields=["license_status", "is_active"])

        response = self.client.patch(
            reverse("super-admin-shop-status", args=[self.shop.id]),
            {"is_active": False, "reason": "License expired."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_active)
        self.assertTrue(self.shop.is_suspended_by_super_admin)
        self.assertTrue(
            SuperAdminAuditLog.objects.filter(
                action="shop_suspended",
                resource_id=self.shop.id,
            ).exists()
        )

        self.client.force_authenticate(self.shop_admin)
        cannot_reactivate = self.client.patch(
            "/api/shop-admin/shop/availability/",
            {"is_active": True},
            format="json",
        )
        self.assertEqual(cannot_reactivate.status_code, 403)

        self.client.force_authenticate(self.admin)
        missing = self.client.patch(
            reverse("super-admin-shop-status", args=[999999]),
            {"is_active": False, "reason": "Missing."},
            format="json",
        )
        self.assertEqual(missing.status_code, 404)

    def test_suspended_existing_order_requires_super_admin_review(self):
        self.shop.license_status = "verified"
        self.shop.is_active = False
        self.shop.is_suspended_by_super_admin = True
        self.shop.save(
            update_fields=[
                "license_status",
                "is_active",
                "is_suspended_by_super_admin",
            ]
        )
        self.order.status = "ready"
        self.order.save(update_fields=["status"])
        review_url = reverse(
            "super-admin-restricted-shop-order-review",
            args=[self.order.id],
        )

        self.client.force_authenticate(self.shop_admin)
        pickup = self.client.post(
            reverse("staff-confirm-handover", args=[self.order.id]),
            staff_confirmation_payload(self.customer),
            format="json",
        )
        unauthorized_review = self.client.post(
            review_url,
            {"decision": "allow_continue"},
            format="json",
        )
        self.assertEqual(pickup.status_code, 403)
        self.assertEqual(unauthorized_review.status_code, 403)

        self.client.force_authenticate(self.admin)
        queue = self.client.get(reverse("super-admin-restricted-shop-orders"))
        self.assertEqual(queue.status_code, 200)
        queued_order = next(
            item for item in queue.data["results"] if item["id"] == self.order.id
        )
        self.assertEqual(queued_order["shop_restriction"], "shop_suspended")
        self.assertEqual(queued_order["review_status"], "pending")
        approved = self.client.post(
            review_url,
            {"decision": "allow_continue", "note": "Accepted order reviewed."},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)
        audit = SuperAdminAuditLog.objects.get(
            action="restricted_order_continuation_authorized",
            resource_id=self.order.id,
        )
        self.assertEqual(audit.actor_id, self.admin.id)
        self.assertIsNotNone(audit.created_at)

        # Restricted-shop continuation and high-value approval are separate gates.
        self.client.force_authenticate(self.admin)
        high_value_approval = self.client.post(
            reverse("super-admin-order-approval", args=[self.order.id]),
            {"decision": "approved"}, format="json",
        )
        self.assertEqual(high_value_approval.status_code, 200)
        self.client.force_authenticate(self.admin)
        forbidden_super_admin = self.client.post(
            reverse("staff-confirm-handover", args=[self.order.id]),
            staff_confirmation_payload(self.customer), format="json",
        )
        self.assertEqual(forbidden_super_admin.status_code, 403)
        record_captured_payment_for_test(self.order)
        self.client.force_authenticate(self.shop_admin)
        pickup = self.client.post(
            reverse("staff-confirm-handover", args=[self.order.id]),
            staff_confirmation_payload(self.customer), format="json",
        )
        self.assertEqual(pickup.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "completed")
        self.assertTrue(self.order.pickup_verified)

    def test_review_authorizes_only_the_existing_order_progression(self):
        self.shop.license_status = "verified"
        self.shop.is_active = False
        self.shop.is_suspended_by_super_admin = True
        self.shop.save(
            update_fields=[
                "license_status",
                "is_active",
                "is_suspended_by_super_admin",
            ]
        )
        self.order.status = "confirmed"
        self.order.pickup_mode = "vehicle"
        self.order.subtotal = Decimal("1000.00")
        self.order.application_fee = Decimal("50.00")
        self.order.total_amount = Decimal("1050.00")
        self.order.save(
            update_fields=[
                "status",
                "pickup_mode",
                "subtotal",
                "application_fee",
                "total_amount",
            ]
        )
        record_captured_payment_for_test(self.order)
        update_url = reverse(
            "shop_admin_update_order_status",
            args=[self.order.id],
        )
        self.client.force_authenticate(self.shop_admin)
        blocked = self.client.post(
            update_url,
            {"status": "preparing"},
            format="json",
        )
        blocked_list = self.client.get(reverse("shop_admin_orders"))
        blocked_detail = self.client.get(
            reverse("shop_admin_order_detail", args=[self.order.id])
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked_list.status_code, 403)
        self.assertEqual(blocked_detail.status_code, 403)

        self.client.force_authenticate(self.admin)
        approved = self.client.post(
            reverse(
                "super-admin-restricted-shop-order-review",
                args=[self.order.id],
            ),
            {"decision": "allow_continue"},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)

        self.client.force_authenticate(self.shop_admin)
        preparing = self.client.post(
            update_url,
            {"status": "preparing"},
            format="json",
        )
        ready = self.client.post(
            update_url,
            {"status": "ready"},
            format="json",
        )
        orders = self.client.get(reverse("shop_admin_orders"))
        detail = self.client.get(
            reverse("shop_admin_order_detail", args=[self.order.id])
        )
        products = self.client.get(reverse("shop_admin_products"))
        self.assertEqual(preparing.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(orders.status_code, 200)
        self.assertEqual([order["id"] for order in orders.data], [self.order.id])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(products.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ready")

    def test_expired_license_and_pending_renewal_require_review(self):
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.is_suspended_by_super_admin = False
        self.shop.save(
            update_fields=[
                "license_status",
                "is_active",
                "is_suspended_by_super_admin",
            ]
        )
        verified_document = self.shop.application_documents.get(
            verification_status="verified"
        )
        verified_document.expiry_date = timezone.localdate() - timedelta(days=1)
        verified_document.save(update_fields=["expiry_date"])
        ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number="PENDING-RENEWAL",
            document="private_shop_licenses/pending-renewal.pdf",
            expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status="pending",
        )
        self.order.status = "ready"
        self.order.pickup_mode = "vehicle"
        self.order.vehicle = CustomerVehicle.objects.create(
            customer=CustomerProfile.objects.create(user=self.customer), vehicle_type="car", vehicle_number="TEST-01",
        )
        self.order.subtotal = Decimal("1000.00")
        self.order.application_fee = Decimal("50.00")
        self.order.total_amount = Decimal("1050.00")
        self.order.save(
            update_fields=[
                "status",
                "pickup_mode",
                "vehicle",
                "subtotal",
                "application_fee",
                "total_amount",
            ]
        )
        record_captured_payment_for_test(self.order)
        review_url = reverse(
            "super-admin-restricted-shop-order-review",
            args=[self.order.id],
        )

        self.client.force_authenticate(self.shop_admin)
        pickup = self.client.post(
            reverse("staff-confirm-handover", args=[self.order.id]),
            staff_confirmation_payload(self.customer),
            format="json",
        )
        location = self.client.post(
            reverse("order-live-location", args=[self.order.id]),
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        self.assertEqual(pickup.status_code, 403)
        self.assertEqual(location.status_code, 403)
        self.assertFalse(self.order.pickup_verified)

        self.client.force_authenticate(self.admin)
        queue = self.client.get(reverse("super-admin-restricted-shop-orders"))
        queued_order = next(
            item for item in queue.data["results"] if item["id"] == self.order.id
        )
        self.assertEqual(queued_order["shop_restriction"], "license_expired")
        approved = self.client.post(
            review_url,
            {"decision": "allow_continue"},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)

        self.client.force_authenticate(self.shop_admin)
        location = self.client.post(
            reverse("order-live-location", args=[self.order.id]),
            {
                "latitude": "18.520430",
                "longitude": "73.856744",
                "is_sharing": True,
            },
            format="json",
        )
        pickup = self.client.post(
            reverse("staff-confirm-handover", args=[self.order.id]),
            staff_confirmation_payload(self.customer),
            format="json",
        )
        self.assertEqual(location.status_code, 200)
        self.assertEqual(pickup.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "completed")
        self.assertTrue(self.order.pickup_verified)

    def test_authorized_license_renewal_reinstates_without_changing_existing_orders(self):
        expired_document = self.shop.application_documents.get(
            verification_status="verified"
        )
        expired_document.expiry_date = timezone.localdate() - timedelta(days=1)
        expired_document.save(update_fields=["expiry_date"])
        self.shop.license_status = "expired"
        self.shop.is_active = False
        self.shop.is_suspended_by_super_admin = False
        self.shop.save(
            update_fields=[
                "license_status",
                "is_active",
                "is_suspended_by_super_admin",
            ]
        )
        self.order.status = "preparing"
        self.order.save(update_fields=["status"])

        self.client.force_authenticate(self.shop_admin)
        renewal_response = self.client.post(
            reverse("shop-admin-onboarding"),
            {
                "shop_name": self.shop.shop_name,
                "phone": self.shop.phone,
                "email": self.shop.email,
                "address_line1": self.shop.address_line1,
                "address_line2": self.shop.address_line2,
                "city": self.shop.city,
                "state": self.shop.state,
                "postal_code": self.shop.postal_code,
                "license_number": "APPROVED-RENEWAL",
                "expiry_date": (
                    timezone.localdate() + timedelta(days=365)
                ).isoformat(),
                "license_document": SimpleUploadedFile(
                    "approved-renewal.pdf",
                    b"%PDF-1.4 renewed license",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
        )
        self.assertEqual(renewal_response.status_code, 201)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "pending")
        self.assertFalse(self.shop.is_active)

        self.client.force_authenticate(self.admin)
        pending_queue = self.client.get(
            reverse("super-admin-restricted-shop-orders")
        )
        pending_order = next(
            item
            for item in pending_queue.data["results"]
            if item["id"] == self.order.id
        )
        self.assertEqual(pending_order["shop_restriction"], "shop_not_approved")

        approved = self.client.post(
            reverse(
                "super-admin-shop-application-approve",
                args=[self.shop.id],
            ),
            {"note": "Renewed license verified."},
            format="json",
        )

        self.assertEqual(approved.status_code, 200)
        renewal = self.shop.application_documents.get(
            license_number="APPROVED-RENEWAL"
        )
        self.shop.refresh_from_db()
        self.assertEqual(renewal.verification_status, "verified")
        self.assertEqual(self.shop.license_status, "verified")
        self.assertTrue(self.shop.is_active)
        self.assertTrue(
            self.shop.application_documents.filter(
                verification_status="verified",
                expiry_date__gt=timezone.localdate(),
            ).exists()
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "preparing")
    def test_high_value_order_approval_can_continue_in_shop_admin_flow(self):
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.save(update_fields=["license_status", "is_active"])
        self.client.force_authenticate(self.shop_admin)
        shop_orders = self.client.get(reverse("shop_admin_orders"))
        self.assertEqual(shop_orders.status_code, 200)
        self.assertEqual(shop_orders.data, [])

        dashboard = self.client.get(reverse("shop_admin_dashboard"))
        self.assertEqual(dashboard.data["new_orders_count"], 0)
        self.assertEqual(dashboard.data["total_orders"], 0)
        self.assertEqual(dashboard.data["todays_orders"], 0)

        self.client.force_authenticate(self.shop_admin)
        blocked = self.client.post(
            f"/api/shop-admin/orders/{self.order.id}/accept/"
        )
        self.assertEqual(blocked.status_code, 403)
        hidden_detail = self.client.get(
            reverse("shop_admin_order_detail", args=[self.order.id])
        )
        self.assertEqual(hidden_detail.status_code, 404)

        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse(
                "super-admin-order-approval",
                args=[self.order.id],
            ),
            {"decision": "approved", "note": "Compliance review passed."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "admin_approved")
        self.assertTrue(
            SuperAdminAuditLog.objects.filter(
                action="high_value_order_approved",
                resource_id=self.order.id,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.customer,
                order=self.order,
                title="High-Value Order Update",
            ).exists()
        )

        self.client.force_authenticate(self.shop_admin)
        shop_orders = self.client.get(reverse("shop_admin_orders"))
        self.assertEqual(shop_orders.status_code, 200)
        self.assertEqual(len(shop_orders.data), 1)
        shop_response = self.client.post(
            reverse("shop-admin-order-decision", args=[self.order.id]),
            {"decision": "accepted"},
            format="json",
        )
        self.assertEqual(shop_response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "confirmed")

    def test_total_threshold_includes_fee_and_legacy_unapproved_orders(self):
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.save(update_fields=["license_status", "is_active"])
        self.order.subtotal = Decimal("14950.00")
        self.order.application_fee = Decimal("50.00")
        self.order.total_amount = Decimal("15000.00")
        self.order.status = "confirmed"
        self.order.save(
            update_fields=[
                "subtotal",
                "application_fee",
                "total_amount",
                "status",
            ]
        )

        high_value_orders = self.client.get(
            reverse("super-admin-high-value-orders")
        )
        self.assertEqual(high_value_orders.status_code, 200)
        self.assertEqual(high_value_orders.data["count"], 1)
        self.assertEqual(
            high_value_orders.data["results"][0]["status"],
            "confirmed",
        )

        pending_orders = self.client.get(reverse("super-admin-pending-orders"))
        self.assertEqual(pending_orders.status_code, 200)
        self.assertEqual(pending_orders.data["count"], 1)

        self.client.force_authenticate(self.shop_admin)
        self.assertEqual(
            self.client.get(reverse("shop_admin_orders")).data,
            [],
        )
        shop_decision = self.client.post(
            reverse("shop-admin-order-decision", args=[self.order.id]),
            {"decision": "accepted"},
            format="json",
        )
        self.assertEqual(shop_decision.status_code, 409)
        shop_status = self.client.post(
            reverse(
                "shop_admin_update_order_status",
                args=[self.order.id],
            ),
            {"status": "preparing"},
            format="json",
        )
        self.assertEqual(shop_status.status_code, 403)

        self.client.force_authenticate(self.admin)
        approval = self.client.post(
            reverse("super-admin-order-approval", args=[self.order.id]),
            {"decision": "approved"},
            format="json",
        )
        self.assertEqual(approval.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "confirmed")
        self.assertIsNotNone(self.order.super_admin_approved_at)

    def test_high_value_order_rejection_restocks_and_cannot_be_repeated(self):
        response = self.client.post(
            reverse(
                "super-admin-high-value-order-reject",
                args=[self.order.id],
            ),
            {"reason": "Compliance review failed."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")
        self.assertEqual(self.product.stock_quantity, 10)

        duplicate = self.client.post(
            reverse(
                "super-admin-high-value-order-reject",
                args=[self.order.id],
            ),
            {"reason": "Repeat decision."},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            SuperAdminAuditLog.objects.filter(
                action="high_value_order_rejected",
                resource_id=self.order.id,
            ).count(),
            1,
        )

    def test_shop_admin_cannot_decide_another_shops_order(self):
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.save(update_fields=["license_status", "is_active"])
        other_admin = User.objects.create_user(
            username="other-shop-admin",
            email="other-shop-admin@example.com",
            password="test-password",
            user_type="shop_admin",
        )
        other_shop = Shop.objects.create(
            owner=other_admin,
            shop_name="Other Shop",
            phone="9876500000",
            email="other-shop@example.com",
            address_line1="2 Other Road",
            city="Mumbai",
            state="Maharashtra",
            postal_code="400001",
            license_number="TEST-LICENSE-2",
            license_status="verified",
            is_active=True,
        )
        ShopLicenseDocument.objects.create(
            shop=other_shop,
            license_number=other_shop.license_number,
            document="private_shop_licenses/other-shop-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )
        other_order = Order.objects.create(
            customer=self.customer,
            shop=other_shop,
            status="pending",
            subtotal=Decimal("500.00"),
            total_amount=Decimal("550.00"),
        )
        self.client.force_authenticate(self.shop_admin)
        response = self.client.post(
            f"/api/shop-admin/orders/{other_order.id}/accept/"
        )
        self.assertEqual(response.status_code, 404)

    def test_notifications_are_scoped_to_authenticated_super_admin(self):
        Notification.objects.create(
            user=self.admin,
            order=self.order,
            notification_type="general",
            title="Admin notification",
            message="Review the order.",
        )
        other_admin = User.objects.create_user(
            username="otheradmin",
            email="otheradmin@example.com",
            password="test-password",
            user_type="super_admin",
        )
        Notification.objects.create(
            user=other_admin,
            order=self.order,
            notification_type="general",
            title="Other admin notification",
            message="Private to another admin.",
        )
        response = self.client.get(reverse("super-admin-notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Admin notification")
        self.assertEqual(
            response.data["results"][0]["notification_type"],
            "general",
        )
        self.assertEqual(
            self.client.get(reverse("super-admin-dashboard")).data[
                "unread_notifications"
            ],
            1,
        )
        marked_read = self.client.post(
            reverse(
                "super-admin-notification-read",
                args=[Notification.objects.get(user=self.admin).id],
            )
        )
        self.assertEqual(marked_read.status_code, 200)
        self.assertEqual(marked_read.data, {
            "id": marked_read.data["id"],
            "is_read": True,
        })
        self.assertEqual(
            self.client.get(reverse("super-admin-dashboard")).data[
                "unread_notifications"
            ],
            0,
        )
        forbidden_read = self.client.post(
            reverse(
                "super-admin-notification-read",
                args=[Notification.objects.get(user=other_admin).id],
            )
        )
        self.assertEqual(forbidden_read.status_code, 404)

    def test_payments_list_uses_order_payment_fields_and_filters(self):
        response = self.client.get(reverse("super-admin-payments"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        payment = response.data["results"][0]
        self.assertEqual(payment["id"], self.order.id)
        self.assertEqual(payment["order_id"], self.order.id)
        self.assertEqual(payment["order_number"], str(self.order.id))
        self.assertEqual(payment["shop"]["id"], self.shop.id)
        self.assertEqual(payment["amount"], "15050.00")
        self.assertEqual(payment["payment_status"], "pending")
        self.assertEqual(payment["payment_method"], "online")
        self.assertNotIn("transaction_reference", payment)

        filtered = self.client.get(
            reverse("super-admin-payments"),
            {
                "payment_status": "pending",
                "order_id": str(self.order.id),
                "shop_id": str(self.shop.id),
                "from": self.order.created_at.astimezone(
                    ZoneInfo("Asia/Kolkata")
                ).date().isoformat(),
                "to": self.order.created_at.astimezone(
                    ZoneInfo("Asia/Kolkata")
                ).date().isoformat(),
                "search": self.shop.shop_name,
                "page": "1",
                "page_size": "1",
            },
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.data["count"], 1)

        invalid_filter = self.client.get(
            reverse("super-admin-payments"),
            {"payment_status": "captured"},
        )
        self.assertEqual(invalid_filter.status_code, 400)

    def test_payment_date_filters_use_india_local_day_bounds(self):
        Order.objects.filter(pk=self.order.pk).update(
            created_at=datetime(
                2026,
                10,
                23,
                19,
                0,
                tzinfo=datetime_timezone.utc,
            )
        )

        utc_date = self.client.get(
            reverse("super-admin-payments"),
            {"from": "2026-10-23", "to": "2026-10-23"},
        )
        self.assertEqual(utc_date.status_code, 200)
        self.assertEqual(utc_date.data["count"], 0)

        india_local_date = self.client.get(
            reverse("super-admin-payments"),
            {"from": "2026-10-24", "to": "2026-10-24"},
        )
        self.assertEqual(india_local_date.status_code, 200)
        self.assertEqual(india_local_date.data["count"], 1)

    def test_payments_endpoint_requires_super_admin(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse("super-admin-payments"))
        self.assertEqual(response.status_code, 401)

        self.client.force_authenticate(self.shop_admin)
        response = self.client.get(reverse("super-admin-payments"))
        self.assertEqual(response.status_code, 403)

    def test_request_changes_accepts_optional_note_and_audits_it(self):
        response = self.client.post(
            reverse(
                "super-admin-shop-application-request-changes",
                args=[self.shop.id],
            ),
            {
                "reason": "Please provide a clearer license document.",
                "note": "The uploaded scan is difficult to read.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {"id": self.shop.id, "application_status": "rejected"},
        )
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "rejected")
        self.assertFalse(self.shop.is_active)
        self.assertFalse(
            self.shop.application_documents.filter(
                verification_status="pending"
            ).exists()
        )
        pending_queue = self.client.get(
            reverse("super-admin-shop-applications")
        )
        self.assertNotIn(
            self.shop.id,
            {item["id"] for item in pending_queue.data["results"]},
        )
        audit = SuperAdminAuditLog.objects.get(
            action="shop_application_changes_requested",
            resource_id=self.shop.id,
        )
        self.assertEqual(
            audit.metadata["reason"],
            "Please provide a clearer license document.",
        )
        self.assertEqual(
            audit.metadata["note"],
            "The uploaded scan is difficult to read.",
        )
        self.assertEqual(audit.metadata["old_status"], "pending")
        self.assertEqual(audit.metadata["new_status"], "rejected")

    def test_requested_changes_can_be_resubmitted_and_reviewed_again(self):
        requested = self.client.post(
            reverse(
                "super-admin-shop-application-request-changes",
                args=[self.shop.id],
            ),
            {"reason": "Upload a clearer license document."},
            format="json",
        )
        self.assertEqual(requested.status_code, 200)
        original_documents = list(self.shop.application_documents.all())
        original_document_statuses = {
            document.id: document.verification_status
            for document in original_documents
        }
        self.assertTrue(original_documents)

        self.client.force_authenticate(self.shop_admin)
        application_url = reverse("shop-admin-application")
        application = self.client.get(application_url)
        self.assertEqual(application.status_code, 200)
        self.assertEqual(
            application.data["review_history"][-1]["reason"],
            "Upload a clearer license document.",
        )

        resubmitted = self.client.post(
            application_url,
            {
                "shop_name": "Corrected Test Shop",
                "phone": "9876543211",
                "email": "corrected-shop@example.com",
                "address_line1": "2 Corrected Road",
                "address_line2": "Unit 4",
                "city": "Pune",
                "state": "Maharashtra",
                "postal_code": "411002",
                "license_number": "TEST-LICENSE-CORRECTED",
                "expiry_date": "2030-12-31",
                "license_document": SimpleUploadedFile(
                    "corrected-license.pdf",
                    b"%PDF-1.4 corrected license",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
        )
        self.assertEqual(resubmitted.status_code, 201)
        self.assertEqual(resubmitted.data["application_status"], "pending")
        self.assertEqual(resubmitted.data["shop_name"], "Corrected Test Shop")
        self.assertEqual(
            resubmitted.data["documents"][0]["verification_status"],
            "pending",
        )
        for document in original_documents:
            document.refresh_from_db()
            expected_status = (
                "rejected"
                if original_document_statuses[document.id] == "pending"
                else original_document_statuses[document.id]
            )
            self.assertEqual(document.verification_status, expected_status)

        self.client.force_authenticate(self.admin)
        queue = self.client.get(reverse("super-admin-shop-applications"))
        queued_ids = {item["id"] for item in queue.data["results"]}
        self.assertIn(self.shop.id, queued_ids)

        detail = self.client.get(
            reverse(
                "super-admin-shop-application-detail",
                args=[self.shop.id],
            )
        )
        changes_event = next(
            entry
            for entry in detail.data["status_history"]
            if entry["action"] == "shop_application_changes_requested"
        )
        self.assertEqual(
            changes_event["metadata"]["reason"],
            "Upload a clearer license document.",
        )

        approved = self.client.post(
            reverse(
                "super-admin-shop-application-approve",
                args=[self.shop.id],
            ),
            {"note": "Corrected document approved."},
            format="json",
        )
        self.assertEqual(approved.status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "verified")
        self.assertTrue(self.shop.is_active)
        self.assertTrue(
            self.shop.application_documents.filter(
                license_number="TEST-LICENSE-CORRECTED",
                verification_status="verified",
            ).exists()
        )

    def test_only_the_owning_shop_admin_can_resubmit_corrections(self):
        self.client.post(
            reverse(
                "super-admin-shop-application-request-changes",
                args=[self.shop.id],
            ),
            {"reason": "Correct the shop details."},
            format="json",
        )
        unrelated_admin = User.objects.create_user(
            username="unrelated-shop-admin",
            email="unrelated-shop-admin@example.com",
            user_type="shop_admin",
        )
        self.client.force_authenticate(unrelated_admin)
        response = self.client.post(
            reverse("shop-admin-application"),
            {
                "shop_name": "Unauthorized Change",
                "phone": "9876543211",
                "address_line1": "Unauthorized Road",
                "city": "Pune",
                "state": "Maharashtra",
                "postal_code": "411002",
                "license_number": "UNRELATED-ADMIN-LICENSE",
                "expiry_date": "2030-12-31",
                "license_document": SimpleUploadedFile(
                    "unrelated-license.pdf",
                    b"%PDF-1.4 unrelated",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.shop_name, "Test Shop")
        self.assertEqual(self.shop.license_status, "rejected")

        self.client.force_authenticate(self.customer)
        forbidden = self.client.post(
            reverse("shop-admin-application"),
            {},
            format="multipart",
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_correction_resubmission_requires_all_fields_and_new_document(self):
        self.client.post(
            reverse(
                "super-admin-shop-application-request-changes",
                args=[self.shop.id],
            ),
            {"reason": "Submit a replacement license document."},
            format="json",
        )
        self.client.force_authenticate(self.shop_admin)
        response = self.client.post(
            reverse("shop-admin-application"),
            {
                "shop_name": "Incomplete Resubmission",
                "phone": "9876543211",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "rejected")
        self.assertEqual(self.shop.shop_name, "Test Shop")

    def test_dashboard_reports_and_platform_lists_use_database_records(self):
        dashboard = self.client.get(reverse("super-admin-dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.data["pending_shop_applications"], 1)
        self.assertEqual(dashboard.data["high_value_orders_pending"], 1)
        self.assertNotIn("pending_refunds", dashboard.data)

        self.shop.application_documents.all().delete()
        applications = self.client.get(reverse("super-admin-shop-applications"))
        self.assertEqual(applications.status_code, 200)
        self.assertEqual(applications.data["count"], 1)
        self.assertEqual(applications.data["results"][0]["documents"], [])

        shops = self.client.get(reverse("super-admin-shops"))
        self.assertEqual(shops.status_code, 200)
        self.assertEqual(shops.data["count"], 1)

        customers = self.client.get(reverse("super-admin-customers"))
        self.assertEqual(customers.status_code, 200)
        self.assertEqual(customers.data["count"], 1)
        self.assertEqual(
            customers.data["results"][0]["phone_masked"],
            "******3210",
        )
        self.assertNotIn("firebase_uid", customers.data["results"][0])

        orders = self.client.get(reverse("super-admin-high-value-orders"))
        self.assertEqual(orders.status_code, 200)
        self.assertEqual(orders.data["count"], 1)

        report = self.client.get(reverse("super-admin-reports"))
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data["orders_count"], 1)
        self.assertEqual(Decimal(report.data["gmv"]), Decimal("15000.00"))
        self.assertEqual(len(report.data["order_trend"]), 7)
        self.assertEqual(report.data["top_shops"][0]["shop_id"], self.shop.id)

    def test_invalid_pagination_and_report_ranges_return_400(self):
        response = self.client.get(
            reverse("super-admin-shops"),
            {"page_size": "not-a-number"},
        )
        self.assertEqual(response.status_code, 400)

    def test_empty_reports_return_zero_metrics_and_empty_trends(self):
        self.order.delete()
        response = self.client.get(reverse("super-admin-reports"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["orders_count"], 0)
        self.assertEqual(response.data["gmv"], "0.00")
        self.assertEqual(response.data["order_trend"], [])
        self.assertEqual(response.data["top_shops"], [])
        self.assertEqual(response.data["category_trends"], [])

        response = self.client.get(
            reverse("super-admin-reports"),
            {"range": "365d"},
        )
        self.assertEqual(response.status_code, 400)

    def test_audit_records_cannot_be_updated_or_deleted(self):
        audit = SuperAdminAuditLog.objects.create(
            actor=self.admin,
            actor_email=self.admin.email,
            action="test",
            resource_type="shop",
            resource_id=self.shop.id,
            summary="Audit test.",
        )
        with self.assertRaises(DjangoValidationError):
            audit.save()
        with self.assertRaises(DjangoValidationError):
            audit.delete()
        with self.assertRaises(DjangoValidationError):
            SuperAdminAuditLog.objects.filter(pk=audit.pk).update(summary="Changed.")
        with self.assertRaises(DjangoValidationError):
            SuperAdminAuditLog.objects.filter(pk=audit.pk).delete()

    def test_super_admin_can_review_verification_and_update_support_ticket(self):
        verification = CustomerVerificationSession.objects.create(
            user=self.customer,
            document=SimpleUploadedFile(
                "identity.pdf",
                b"%PDF-1.4 test",
                content_type="application/pdf",
            ),
            document_type="government_id",
        )
        response = self.client.post(
            reverse("super-admin-verification-sessions"),
            {
                "session_id": verification.id,
                "status": "approved",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        verification.refresh_from_db()
        self.assertEqual(verification.status, "approved")
        self.assertEqual(verification.reviewed_by, self.admin)

        ticket = SupportTicket.objects.create(
            user=self.customer,
            subject="Order question",
            message="Need help.",
        )
        response = self.client.patch(
            reverse("super-admin-support-ticket-detail", args=[ticket.id]),
            {
                "status": "resolved",
                "admin_response": "Resolved.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "resolved")
        self.assertEqual(ticket.admin_response, "Resolved.")

    def test_age_proof_approval_records_confirmed_dob_and_trusted_age(self):
        verification = CustomerVerificationSession.objects.create(
            user=self.customer,
            document=SimpleUploadedFile(
                "age-proof.pdf",
                b"%PDF-1.4 age proof",
                content_type="application/pdf",
            ),
            document_type="age_proof",
        )
        response = self.client.post(
            reverse("super-admin-verification-sessions"),
            {
                "session_id": verification.id,
                "status": "approved",
                "confirmed_date_of_birth": "2000-01-01",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["confirmed_date_of_birth"], "2000-01-01")
        self.assertTrue(response.data["age_verified"])
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.date_of_birth, date(2000, 1, 1))
        self.assertTrue(self.customer.age_verified)
        verification.refresh_from_db()
        self.assertEqual(verification.reviewed_by, self.admin)

    def test_age_proof_approval_does_not_verify_a_customer_under_21(self):
        verification = CustomerVerificationSession.objects.create(
            user=self.customer,
            document=SimpleUploadedFile(
                "age-proof.pdf",
                b"%PDF-1.4 age proof",
                content_type="application/pdf",
            ),
            document_type="age_proof",
        )
        response = self.client.post(
            reverse("super-admin-verification-sessions"),
            {
                "session_id": verification.id,
                "status": "approved",
                "confirmed_date_of_birth": "2010-01-01",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["age_verified"])
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.date_of_birth, date(2010, 1, 1))
        self.assertFalse(self.customer.age_verified)
        self.assertIsNone(self.customer.age_verified_at)

    def test_threshold_is_fixed_while_policy_publish_still_works(self):
        response = self.client.post(
            reverse("super-admin-legal-policies"),
            {
                "slug": "terms",
                "title": "Terms of Service",
                "version": "1.0",
                "content": "Terms text.",
                "is_published": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        policy = LegalPolicy.objects.get(slug="terms")
        self.assertTrue(policy.is_published)

        response = self.client.patch(
            reverse("super-admin-settings"),
            {"high_value_order_threshold": "20000.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["high_value_order_threshold"][0], (
            "The high-value order threshold is fixed at 15000.00 and "
            "cannot be changed."
        ))
        self.assertEqual(
            PlatformSettings.objects.get(pk=1).high_value_order_threshold,
            Decimal("15000.00"),
        )
        settings_response = self.client.get(reverse("super-admin-settings"))
        self.assertEqual(settings_response.status_code, 200)
        self.assertEqual(
            settings_response.data["high_value_order_threshold"],
            "15000.00",
        )

    def test_super_admin_can_read_and_update_nearby_shop_radius(self):
        response = self.client.get(reverse("super-admin-settings"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["nearby_shop_radius_km"], "10.00")

        response = self.client.patch(
            reverse("super-admin-settings"),
            {"nearby_shop_radius_km": "25.50"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["nearby_shop_radius_km"], "25.50")
        settings = PlatformSettings.objects.get(pk=1)
        self.assertEqual(settings.nearby_shop_radius_km, Decimal("25.50"))
        self.assertEqual(settings.high_value_order_threshold, Decimal("15000.00"))

    def test_super_admin_cannot_set_invalid_nearby_shop_radius(self):
        for radius in ("0", "0.09", "100.01", "1.234", "not-a-number"):
            with self.subTest(radius=radius):
                response = self.client.patch(
                    reverse("super-admin-settings"),
                    {"nearby_shop_radius_km": radius},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
