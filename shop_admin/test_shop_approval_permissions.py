from datetime import date, timedelta
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop, ShopLicenseDocument


class ShopAdminApprovalPermissionTests(TestCase):
    OPERATIONAL_URLS = (
        ("shop_admin_profile", ()),
        ("shop_admin_me", ()),
        ("shop_admin_update_shop", ()),
        ("shop_admin_update_shop_availability", ()),
        ("shop_admin_inventory", ()),
        ("shop_admin_adjust_inventory", (999,)),
        ("shop_admin_dashboard", ()),
        ("shop_admin_notifications", ()),
        ("shop_admin_mark_notification_read", (999,)),
        ("shop_admin_mark_all_notifications_read", ()),
        ("shop_admin_notification_preferences", ()),
        ("shop_admin_push_token", ()),
        ("shop_admin_products", ()),
        ("shop_admin_product_detail", (999,)),
        ("shop_admin_product_image", (999,)),
        ("shop_admin_bulk_import_products", ()),
        ("shop_admin_reports", ()),
        ("shop_admin_orders", ()),
        ("shop_admin_order_detail", (999,)),
        ("shop-admin-order-decision", (999,)),
        ("shop_admin_accept_order", (999,)),
        ("shop_admin_reject_order", (999,)),
        ("shop_admin_keep_order_pending", (999,)),
        ("shop_admin_update_order_status", (999,)),
        ("shop-profile", ()),
    )

    def setUp(self):
        media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(media_dir.cleanup)
        settings_override = override_settings(MEDIA_ROOT=media_dir.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.client = APIClient()
        self.shop_admin = User.objects.create_user(
            username="approval-gate-shop-admin",
            email="approval-gate@example.com",
            password="test-password",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Approval Gate Shop",
            phone="9876543210",
            email="approval-gate@example.com",
            address_line1="1 Test Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="APPROVAL-GATE-LICENSE",
            license_status="pending",
            is_active=False,
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/approval-gate-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )
        self.client.force_authenticate(self.shop_admin)

    def request_all_operational_routes(self):
        responses = []
        for name, args in self.OPERATIONAL_URLS:
            url = reverse(name, args=args)
            if name == "shop_admin_update_order_status":
                response = self.client.post(
                    url,
                    {"status": "preparing"},
                    format="json",
                )
            else:
                response = self.client.get(url)
            responses.append(response)
        return responses

    def test_all_operational_routes_require_verified_active_unsuspended_shop(self):
        unavailable_states = (
            ("pending", False, False),
            ("rejected", False, False),
            ("verified", False, False),
            ("verified", True, True),
            ("expired", False, False),
        )
        for license_status, is_active, is_suspended in unavailable_states:
            with self.subTest(
                license_status=license_status,
                is_active=is_active,
                is_suspended=is_suspended,
            ):
                self.shop.license_status = license_status
                self.shop.is_active = is_active
                self.shop.is_suspended_by_super_admin = is_suspended
                self.shop.save(
                    update_fields=[
                        "license_status",
                        "is_active",
                        "is_suspended_by_super_admin",
                    ]
                )
                responses = self.request_all_operational_routes()
                self.assertEqual(
                    [response.status_code for response in responses],
                    [403] * len(self.OPERATIONAL_URLS),
                )

    def test_approved_active_shop_can_reach_each_operational_route(self):
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

        responses = self.request_all_operational_routes()
        self.assertEqual(len(responses), len(self.OPERATIONAL_URLS))
        self.assertTrue(
            all(response.status_code != 403 for response in responses),
            [
                (self.OPERATIONAL_URLS[index][0], response.status_code)
                for index, response in enumerate(responses)
                if response.status_code == 403
            ],
        )

    def test_closed_shop_remains_operational_for_existing_orders(self):
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.is_open = False
        self.shop.save(
            update_fields=["license_status", "is_active", "is_open"]
        )

        responses = self.request_all_operational_routes()

        self.assertTrue(
            all(response.status_code != 403 for response in responses),
            [
                (self.OPERATIONAL_URLS[index][0], response.status_code)
                for index, response in enumerate(responses)
                if response.status_code == 403
            ],
        )

    def test_expired_verified_license_blocks_even_with_pending_renewal(self):
        verified_document = self.shop.application_documents.get(
            verification_status="verified"
        )
        verified_document.expiry_date = timezone.localdate() - timedelta(days=1)
        verified_document.save(update_fields=["expiry_date"])
        ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number="APPROVAL-GATE-RENEWAL",
            document="private_shop_licenses/pending-renewal.pdf",
            expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status="pending",
        )
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.save(
            update_fields=["license_status", "is_active"]
        )

        responses = self.request_all_operational_routes()

        self.assertEqual(
            [response.status_code for response in responses],
            [403] * len(self.OPERATIONAL_URLS),
        )

    def test_missing_verified_license_document_fails_closed(self):
        self.shop.application_documents.all().delete()
        self.shop.license_status = "verified"
        self.shop.is_active = True
        self.shop.save(
            update_fields=["license_status", "is_active"]
        )

        responses = self.request_all_operational_routes()

        self.assertEqual(
            [response.status_code for response in responses],
            [403] * len(self.OPERATIONAL_URLS),
        )

    def test_super_admin_and_customer_do_not_gain_shop_admin_access(self):
        for role in ("super_admin", "customer"):
            user = User.objects.create_user(
                username=f"approval-gate-{role}",
                email=f"approval-gate-{role}@example.com",
                password="test-password",
                user_type=role,
            )
            self.client.force_authenticate(user)
            responses = self.request_all_operational_routes()
            self.assertEqual(
                [response.status_code for response in responses],
                [403] * len(self.OPERATIONAL_URLS),
            )

    def test_pending_and_rejected_applicants_can_view_application_status(self):
        application_url = reverse("shop-admin-application")
        for status_value in ("pending", "rejected"):
            with self.subTest(status=status_value):
                self.shop.license_status = status_value
                self.shop.is_active = False
                self.shop.save(update_fields=["license_status", "is_active"])
                response = self.client.get(application_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["license_status"], status_value)

    def test_pending_applicant_can_view_and_download_own_application_document(self):
        document = ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number="APPROVAL-GATE-DOCUMENT",
            document=SimpleUploadedFile(
                "license.pdf",
                b"test license document",
                content_type="application/pdf",
            ),
            expiry_date=date(2030, 1, 1),
        )

        detail = self.client.get(
            reverse("shop-application-document-detail", args=[document.id])
        )
        self.assertEqual(detail.status_code, 200)

    def test_rejected_applicant_can_resubmit_application(self):
        self.shop.license_status = "rejected"
        self.shop.is_active = False
        self.shop.save(update_fields=["license_status", "is_active"])

        response = self.client.post(
            reverse("shop-admin-application"),
            {
                "shop_name": "Approval Gate Shop Resubmitted",
                "phone": "9876543210",
                "email": "approval-gate@example.com",
                "address_line1": "2 Test Road",
                "city": "Pune",
                "state": "Maharashtra",
                "postal_code": "411001",
                "license_number": "APPROVAL-GATE-LICENSE",
                "license_document": SimpleUploadedFile(
                    "license.pdf",
                    b"test license document",
                    content_type="application/pdf",
                ),
                "expiry_date": date(2030, 1, 1),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "pending")
        self.assertFalse(self.shop.is_active)
