from datetime import timedelta
from decimal import Decimal
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop


class ShopOnboardingCoordinateTests(TestCase):
    def setUp(self):
        media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(media_dir.cleanup)
        setting_override = override_settings(MEDIA_ROOT=media_dir.name)
        setting_override.enable()
        self.addCleanup(setting_override.disable)

        self.applicant = User.objects.create_user(
            username="onboarding-coordinate-applicant",
            email="onboarding-coordinate-applicant@example.com",
            password="StrongPassword#123",
            user_type="customer",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.applicant)

    def onboarding_url(self):
        return reverse("shop-admin-onboarding")

    def payload(self, **overrides):
        data = {
            "shop_name": "Coordinate Onboarding Shop",
            "phone": "9876543210",
            "email": "coordinates@example.com",
            "address_line1": "1 Coordinate Road",
            "city": "Pune",
            "state": "Maharashtra",
            "postal_code": "411001",
            "license_number": "COORDINATE-ONBOARDING-LICENSE",
            "expiry_date": str(timezone.localdate() + timedelta(days=365)),
            "license_document": SimpleUploadedFile(
                "license.pdf",
                b"%PDF-1.4 coordinate onboarding license",
                content_type="application/pdf",
            ),
        }
        data.update(overrides)
        return data

    def submit(self, **overrides):
        return self.client.post(
            self.onboarding_url(), self.payload(**overrides), format="multipart"
        )

    def test_valid_coordinate_pair_is_persisted_and_returned(self):
        response = self.submit(latitude="18.520430", longitude="73.856744")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["latitude"], "18.520430")
        self.assertEqual(response.data["longitude"], "73.856744")
        shop = Shop.objects.get(owner=self.applicant)
        self.assertEqual(shop.latitude, Decimal("18.520430"))
        self.assertEqual(shop.longitude, Decimal("73.856744"))

    def test_omitted_coordinates_remain_optional_and_return_null(self):
        response = self.submit()

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data["latitude"])
        self.assertIsNone(response.data["longitude"])
        shop = Shop.objects.get(owner=self.applicant)
        self.assertIsNone(shop.latitude)
        self.assertIsNone(shop.longitude)

    def test_only_one_coordinate_is_rejected(self):
        for data in (
            {"latitude": "18.520430"},
            {"longitude": "73.856744"},
        ):
            with self.subTest(data=data):
                response = self.submit(**data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("non_field_errors", response.data)
                self.assertEqual(Shop.objects.count(), 0)

    def test_non_finite_non_numeric_and_out_of_range_coordinates_are_rejected(self):
        invalid_pairs = (
            ("91", "73.856744"),
            ("-90.000001", "73.856744"),
            ("18.520430", "180.000001"),
            ("18.520430", "-180.000001"),
            ("not-a-number", "73.856744"),
            ("18.520430", "not-a-number"),
            ("NaN", "73.856744"),
            ("18.520430", "Infinity"),
            ("-Infinity", "73.856744"),
        )
        for latitude, longitude in invalid_pairs:
            with self.subTest(latitude=latitude, longitude=longitude):
                response = self.submit(latitude=latitude, longitude=longitude)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Shop.objects.count(), 0)

    def test_coordinate_precision_and_multipart_license_document_are_preserved(self):
        response = self.submit(latitude="-12.345678", longitude="179.999999")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["latitude"], "-12.345678")
        self.assertEqual(response.data["longitude"], "179.999999")
        self.assertEqual(len(response.data["documents"]), 1)
        self.assertEqual(response.data["documents"][0]["verification_status"], "pending")

    def test_rejected_shop_resubmission_omitting_coordinates_preserves_existing_location(self):
        self.applicant.user_type = "shop_admin"
        self.applicant.save(update_fields=["user_type"])
        shop = Shop.objects.create(
            owner=self.applicant,
            shop_name="Existing Coordinate Shop",
            phone="9876543210",
            email="coordinates@example.com",
            address_line1="1 Coordinate Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="COORDINATE-ONBOARDING-LICENSE",
            license_status="rejected",
            is_active=False,
            latitude=Decimal("18.520430"),
            longitude=Decimal("73.856744"),
        )

        response = self.submit()

        self.assertEqual(response.status_code, 201)
        shop.refresh_from_db()
        self.assertEqual(shop.latitude, Decimal("18.520430"))
        self.assertEqual(shop.longitude, Decimal("73.856744"))
        self.assertEqual(response.data["latitude"], "18.520430")
        self.assertEqual(response.data["longitude"], "73.856744")

    def test_unauthenticated_onboarding_is_rejected(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            self.onboarding_url(), self.payload(), format="multipart"
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Shop.objects.count(), 0)
