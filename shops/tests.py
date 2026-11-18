from datetime import date, timedelta
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from products.models import Product
from shops.models import Shop, ShopLicenseDocument
from shop_admin.models import PlatformSettings


class ShopCoordinateValidationTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.shop_admin = User.objects.create_user(
            username="coordinate-shop-admin",
            email="coordinate-shop-admin@example.com",
            password="test-password",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Coordinate Test Shop",
            phone="9876500000",
            address_line1="1 Test Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="COORDINATE-TEST-LICENSE",
            license_status="verified",
            is_active=True,
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/coordinate-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.shop_admin)

    def _onboarding_payload(self, license_number, **coordinates):
        return {
            "shop_name": "Onboarding Coordinate Shop",
            "phone": "9876500001",
            "address_line1": "2 Test Road",
            "city": "Pune",
            "state": "Maharashtra",
            "postal_code": "411002",
            "license_number": license_number,
            "expiry_date": "2030-12-31",
            "license_document": SimpleUploadedFile(
                "license.pdf",
                b"%PDF-1.4 test",
                content_type="application/pdf",
            ),
            **coordinates,
        }

    def _submit_onboarding(self, username, email, license_number, **coordinates):
        applicant = User.objects.create_user(
            username=username,
            email=email,
            password="test-password",
            user_type="shop_admin",
        )
        self.client.force_authenticate(applicant)
        return self.client.post(
            reverse("shop-admin-onboarding"),
            self._onboarding_payload(license_number, **coordinates),
            format="multipart",
        )

    def test_shop_profile_update_accepts_valid_and_boundary_coordinates(self):
        response = self.client.patch(
            reverse("shop-profile"),
            {"latitude": "90.000000", "longitude": "-180.000000"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(str(self.shop.latitude), "90.000000")
        self.assertEqual(str(self.shop.longitude), "-180.000000")

        response = self.client.patch(
            reverse("shop-profile"),
            {"latitude": "-45.123456", "longitude": "120.654321"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(str(self.shop.latitude), "-45.123456")
        self.assertEqual(str(self.shop.longitude), "120.654321")

    def test_shop_profile_update_rejects_out_of_range_coordinates(self):
        for field, value in (
            ("latitude", "90.000001"),
            ("latitude", "-90.000001"),
            ("longitude", "180.000001"),
            ("longitude", "-180.000001"),
            ("latitude", "NaN"),
            ("latitude", "Infinity"),
            ("longitude", "-Infinity"),
            ("longitude", True),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.patch(
                    reverse("shop-profile"),
                    {field: value},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.latitude)
        self.assertIsNone(self.shop.longitude)

    def test_nearby_shops_reject_non_finite_and_malformed_coordinates(self):
        url = reverse("nearby-shops")
        for field, value in (
            ("latitude", "NaN"),
            ("latitude", "Infinity"),
            ("latitude", "91"),
            ("longitude", "-Infinity"),
            ("longitude", "not-a-number"),
            ("longitude", "181"),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.get(
                    url,
                    {
                        "latitude": "0",
                        "longitude": "0",
                        field: value,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

    def test_onboarding_accepts_boundary_coordinates(self):
        response = self._submit_onboarding(
            "onboarding-boundary",
            "onboarding-boundary@example.com",
            "ONBOARDING-BOUNDARY-LICENSE",
            latitude="-90.000000",
            longitude="180.000000",
        )

        self.assertEqual(response.status_code, 201)
        shop = Shop.objects.get(license_number="ONBOARDING-BOUNDARY-LICENSE")
        self.assertEqual(str(shop.latitude), "-90.000000")
        self.assertEqual(str(shop.longitude), "180.000000")

    def test_onboarding_rejects_out_of_range_coordinates(self):
        for username, email, license_number, field, value in (
            (
                "onboarding-invalid-lat",
                "onboarding-invalid-lat@example.com",
                "ONBOARDING-INVALID-LAT-LICENSE",
                "latitude",
                "90.000001",
            ),
            (
                "onboarding-invalid-lon",
                "onboarding-invalid-lon@example.com",
                "ONBOARDING-INVALID-LON-LICENSE",
                "longitude",
                "-180.000001",
            ),
        ):
            with self.subTest(field=field, value=value):
                response = self._submit_onboarding(
                    username,
                    email,
                    license_number,
                    **{field: value},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
                self.assertFalse(
                    Shop.objects.filter(license_number=license_number).exists()
                )

    def test_onboarding_keeps_coordinates_optional(self):
        response = self._submit_onboarding(
            "onboarding-no-coordinates",
            "onboarding-no-coordinates@example.com",
            "ONBOARDING-NO-COORDINATES-LICENSE",
        )

        self.assertEqual(response.status_code, 201)
        shop = Shop.objects.get(
            license_number="ONBOARDING-NO-COORDINATES-LICENSE"
        )
        self.assertIsNone(shop.latitude)
        self.assertIsNone(shop.longitude)


class NearbyShopRadiusTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="nearby-customer",
            email="nearby-customer@example.com",
            password="nearby-test-password",
            user_type="customer",
        )
        self.nearby_shop = Shop.objects.create(
            owner=User.objects.create_user(
                username="nearby-shop-admin",
                email="nearby-shop-admin@example.com",
                password="nearby-test-password",
                user_type="shop_admin",
            ),
            shop_name="Nearby Shop",
            phone="9876500002",
            address_line1="1 Nearby Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411003",
            latitude="0.000000",
            longitude="0.140000",
            license_number="NEARBY-TEST-LICENSE",
            license_status="verified",
            is_active=True,
        )
        ShopLicenseDocument.objects.create(
            shop=self.nearby_shop,
            license_number=self.nearby_shop.license_number,
            document="private_shop_licenses/nearby-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.customer)

    def test_nearby_shops_use_configured_radius(self):
        PlatformSettings.objects.create(nearby_shop_radius_km="10.00")
        url = reverse("nearby-shops")

        response = self.client.get(
            url,
            {"latitude": "0", "longitude": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["radius_km"], 10.0)
        self.assertEqual(response.data["count"], 0)

        settings = PlatformSettings.objects.get(pk=1)
        settings.nearby_shop_radius_km = "20.00"
        settings.save(update_fields=["nearby_shop_radius_km"])

        response = self.client.get(
            url,
            {"latitude": "0", "longitude": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["radius_km"], 20.0)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["shops"][0]["id"], self.nearby_shop.id)


class ShopEligibilityDiscoveryTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="eligibility-customer",
            email="eligibility-customer@example.com",
            password="test-password",
            user_type="customer",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.customer)
        today = date.today()
        self.shops = {}
        shop_states = (
            ("open", "verified", True, True, False, today + timedelta(days=365)),
            ("closed", "verified", True, False, False, today + timedelta(days=365)),
            ("suspended", "verified", True, True, True, today + timedelta(days=365)),
            ("expired", "verified", True, True, False, today - timedelta(days=1)),
            ("pending", "pending", False, True, False, today + timedelta(days=365)),
            ("rejected", "rejected", False, True, False, today + timedelta(days=365)),
        )

        for suffix, license_status, is_active, is_open, suspended, expiry in shop_states:
            owner = User.objects.create_user(
                username=f"eligibility-{suffix}",
                email=f"eligibility-{suffix}@example.com",
                password="test-password",
                user_type="shop_admin",
            )
            shop = Shop.objects.create(
                owner=owner,
                shop_name=f"{suffix.title()} Shop",
                phone="9876500000",
                address_line1="1 Eligibility Road",
                city="Pune",
                state="Maharashtra",
                postal_code="411001",
                license_number=f"ELIGIBILITY-{suffix.upper()}",
                license_status=license_status,
                is_active=is_active,
                is_open=is_open,
                is_suspended_by_super_admin=suspended,
            )
            ShopLicenseDocument.objects.create(
                shop=shop,
                license_number=shop.license_number,
                document=f"private_shop_licenses/{suffix}.pdf",
                expiry_date=expiry,
                verification_status=(
                    "verified" if license_status == "verified" else "pending"
                ),
            )
            Product.objects.create(
                shop=shop,
                name=f"{suffix.title()} Product",
                category="other",
                price="1000.00",
                stock_quantity=5,
            )
            self.shops[suffix] = shop

    def test_customer_discovery_excludes_unavailable_shop_states(self):
        shop_response = self.client.get(reverse("customer-shop-list"))
        product_response = self.client.get(reverse("customer-product-list"))

        self.assertEqual(shop_response.status_code, 200)
        self.assertEqual(
            [shop["id"] for shop in shop_response.data["results"]],
            [self.shops["open"].id],
        )
        self.assertEqual(product_response.status_code, 200)
        self.assertEqual(
            [
                product["shop_id"]
                for product in product_response.data["results"]
            ],
            [self.shops["open"].id],
        )
        self.assertEqual(
            self.client.get(
                reverse("customer-shop-detail", args=[self.shops["closed"].id])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "customer-shop-product-list",
                    args=[self.shops["closed"].id],
                )
            ).data["results"],
            [],
        )

    def test_customer_discovery_uses_updated_shop_coordinates(self):
        shop = self.shops["open"]
        shop.latitude = "0.000000"
        shop.longitude = "0.200000"
        shop.save(update_fields=["latitude", "longitude"])
        PlatformSettings.objects.create(nearby_shop_radius_km="10.00")

        nearby_url = reverse("nearby-shops")
        initially_outside = self.client.get(
            nearby_url,
            {"latitude": "0", "longitude": "0"},
        )
        self.assertEqual(initially_outside.status_code, 200)
        self.assertEqual(initially_outside.data["count"], 0)

        # Discovery reads only the approved coordinates on the authoritative shop.
        shop.latitude = "0.000000"
        shop.longitude = "0.050000"
        shop.save(update_fields=["latitude", "longitude"])

        shop.refresh_from_db()
        self.assertEqual(str(shop.latitude), "0.000000")
        self.assertEqual(str(shop.longitude), "0.050000")

        customer_list = self.client.get(reverse("customer-shop-list"))
        self.assertEqual(customer_list.status_code, 200)
        listed_shop = next(
            item
            for item in customer_list.data["results"]
            if item["id"] == shop.id
        )
        self.assertEqual(listed_shop["latitude"], "0.000000")
        self.assertEqual(listed_shop["longitude"], "0.050000")

        nearby = self.client.get(
            nearby_url,
            {"latitude": "0", "longitude": "0"},
        )
        self.assertEqual(nearby.status_code, 200)
        self.assertEqual(nearby.data["count"], 1)
        nearby_shop = nearby.data["shops"][0]
        self.assertEqual(nearby_shop["id"], shop.id)
        self.assertEqual(nearby_shop["latitude"], "0.000000")
        self.assertEqual(nearby_shop["longitude"], "0.050000")
        self.assertAlmostEqual(nearby_shop["distance_km"], 5.56, places=1)
