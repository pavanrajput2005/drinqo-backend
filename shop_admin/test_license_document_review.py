from datetime import timedelta
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from shop_admin.models import SuperAdminAuditLog
from shops.models import Shop, ShopLicenseDocument


class LicenseDocumentReviewWorkflowTests(TestCase):
    def setUp(self):
        media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(media_dir.cleanup)
        setting_override = override_settings(MEDIA_ROOT=media_dir.name)
        setting_override.enable()
        self.addCleanup(setting_override.disable)

        self.super_admin = User.objects.create_user(
            username="license-review-super-admin",
            email="license-review-super-admin@example.com",
            password="StrongPassword#123",
            user_type="super_admin",
        )
        self.shop_admin = User.objects.create_user(
            username="license-review-shop-admin",
            email="license-review-shop-admin@example.com",
            password="StrongPassword#123",
            user_type="shop_admin",
        )
        self.customer = User.objects.create_user(
            username="license-review-customer",
            email="license-review-customer@example.com",
            password="StrongPassword#123",
            user_type="customer",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Active License Review Shop",
            phone="9876543210",
            email="shop@example.com",
            address_line1="1 Review Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="REVIEW-LICENSE-001",
            license_status="verified",
            is_active=True,
        )
        self.pending_document = ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document=SimpleUploadedFile(
                "pending-review.pdf",
                b"%PDF-1.4 pending review license",
                content_type="application/pdf",
            ),
            expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status="pending",
        )
        self.client = APIClient()

    def pending_queue_url(self):
        return reverse("super-admin-pending-license-documents")

    def review_url(self, document=None):
        return reverse(
            "super-admin-shop-license-review",
            args=[self.shop.id, (document or self.pending_document).id],
        )

    def test_pending_queue_includes_active_shop_document_and_secure_download_url(self):
        self.client.force_authenticate(self.super_admin)

        response = self.client.get(self.pending_queue_url())

        self.assertEqual(response.status_code, 200)
        entry = next(
            item
            for item in response.data["results"]
            if item["id"] == self.pending_document.id
        )
        self.assertEqual(entry["verification_status"], "pending")
        self.assertEqual(entry["shop"]["id"], self.shop.id)
        self.assertTrue(entry["shop"]["is_active"])
        self.assertIn(
            f"/api/shop-admin/application/documents/{self.pending_document.id}/download/",
            entry["download_url"],
        )

    def test_pending_queue_returns_paginated_contract(self):
        self.client.force_authenticate(self.super_admin)

        response = self.client.get(self.pending_queue_url(), {"page": 1, "page_size": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"count", "next", "previous", "results"})
        self.assertEqual(response.data["count"], 1)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        self.assertEqual(len(response.data["results"]), 1)

    def test_non_super_admins_cannot_list_or_review_documents(self):
        for user in (self.shop_admin, self.customer):
            with self.subTest(user_type=user.user_type):
                self.client.force_authenticate(user)
                self.assertEqual(self.client.get(self.pending_queue_url()).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        self.review_url(), {"decision": "verify"}, format="json"
                    ).status_code,
                    403,
                )
        self.pending_document.refresh_from_db()
        self.assertEqual(self.pending_document.verification_status, "pending")

    def test_super_admin_verification_unlocks_operational_dashboard_and_audits(self):
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(
            self.review_url(), {"decision": "verify"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["verification_status"], "verified")
        self.pending_document.refresh_from_db()
        self.assertEqual(self.pending_document.verification_status, "verified")
        self.assertIsNotNone(self.pending_document.verified_at)
        self.assertTrue(
            SuperAdminAuditLog.objects.filter(
                action="shop_license_document_verified",
                resource_id=self.pending_document.id,
            ).exists()
        )

        self.client.force_authenticate(self.shop_admin)
        self.assertEqual(
            self.client.get(reverse("shop_admin_dashboard")).status_code,
            200,
        )

    def test_rejection_requires_reason_and_does_not_unlock_dashboard(self):
        self.client.force_authenticate(self.super_admin)
        self.assertEqual(
            self.client.post(
                self.review_url(), {"decision": "reject"}, format="json"
            ).status_code,
            400,
        )

        response = self.client.post(
            self.review_url(),
            {"decision": "reject", "reason": "Document is unreadable."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.pending_document.refresh_from_db()
        self.assertEqual(self.pending_document.verification_status, "rejected")
        self.assertTrue(
            SuperAdminAuditLog.objects.filter(
                action="shop_license_document_rejected",
                resource_id=self.pending_document.id,
            ).exists()
        )

        self.client.force_authenticate(self.shop_admin)
        self.assertEqual(
            self.client.get(reverse("shop_admin_dashboard")).status_code,
            403,
        )

    def test_expired_pending_document_cannot_unlock_dashboard(self):
        self.pending_document.expiry_date = timezone.localdate() - timedelta(days=1)
        self.pending_document.save(update_fields=["expiry_date"])
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(
            self.review_url(), {"decision": "verify"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.pending_document.refresh_from_db()
        self.assertEqual(self.pending_document.verification_status, "pending")
        self.client.force_authenticate(self.shop_admin)
        self.assertEqual(
            self.client.get(reverse("shop_admin_dashboard")).status_code,
            403,
        )

    def test_duplicate_pending_submission_is_rejected_without_creating_another_document(self):
        self.client.force_authenticate(self.shop_admin)
        response = self.client.post(
            reverse("shop-admin-license-document-upload"),
            {
                "license_number": self.shop.license_number,
                "expiry_date": str(timezone.localdate() + timedelta(days=730)),
                "license_document": SimpleUploadedFile(
                    "another-license.pdf",
                    b"%PDF-1.4 replacement license",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"], "A license document is already pending review."
        )
        self.assertEqual(
            ShopLicenseDocument.objects.filter(
                shop=self.shop, verification_status="pending"
            ).count(),
            1,
        )

    def test_document_download_is_limited_to_owner_or_super_admin(self):
        url = reverse(
            "shop-application-document-download", args=[self.pending_document.id]
        )
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(url).status_code, 403)

        for user in (self.shop_admin, self.super_admin):
            with self.subTest(role=user.user_type):
                self.client.force_authenticate(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 pending review license")
