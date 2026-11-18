from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from shop_admin.models import SuperAdminAuditLog
from shops.models import Shop, ShopLicenseDocument, ShopLocationChangeRequest


class ShopLocationChangeRequestTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="location-owner", email="location-owner@example.com", password="x", user_type="shop_admin")
        self.other = User.objects.create_user(username="location-other", email="location-other@example.com", password="x", user_type="shop_admin")
        self.super_admin = User.objects.create_user(username="location-super", email="location-super@example.com", password="x", user_type="super_admin")
        self.shop = self._shop(self.owner, "Location Owner", "LOC-OWNER", None, None, is_active=False)
        self.other_shop = self._shop(self.other, "Location Other", "LOC-OTHER", "18.1", "73.1")
        self.client = APIClient()

    def _shop(self, owner, name, license_number, latitude, longitude, is_active=True):
        shop = Shop.objects.create(owner=owner, shop_name=name, phone="9876543210", address_line1="Road", city="Pune", state="MH", postal_code="411001", license_number=license_number, license_status="verified", is_active=is_active, latitude=latitude, longitude=longitude)
        ShopLicenseDocument.objects.create(shop=shop, license_number=license_number, document="private_shop_licenses/test.pdf", expiry_date=date(2030, 1, 1), verification_status="verified")
        return shop

    def post_request(self, **payload):
        self.client.force_authenticate(self.owner)
        return self.client.post(reverse("shop-admin-location-requests"), payload or {"latitude": "18.520430", "longitude": "73.856744", "reason": "Licensed premises correction."}, format="json")

    def test_inactive_shop_can_request_first_location_but_cannot_directly_patch(self):
        created = self.post_request()
        self.assertEqual(created.status_code, 201)
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.latitude)
        self.assertEqual(created.data["status"], "pending")
        direct = self.client.patch(reverse("shop_admin_update_shop"), {"latitude": "18.5", "longitude": "73.8"}, format="json")
        self.assertEqual(direct.status_code, 403)  # operational profile remains protected

    def test_location_endpoint_is_owned_and_pending_is_idempotently_blocked(self):
        self.assertEqual(self.post_request().status_code, 201)
        self.assertEqual(self.post_request().status_code, 409)
        response = self.client.get(reverse("shop-admin-location"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["has_location"])
        self.assertEqual(response.data["pending_request"]["status"], "pending")
        self.client.force_authenticate(self.other)
        history = self.client.get(reverse("shop-admin-location-requests"))
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data["results"], [])

    def test_validation_rejects_partial_nonfinite_and_unchanged_coordinates(self):
        for payload in ({"latitude": "18", "reason": "x"}, {"latitude": "NaN", "longitude": "73", "reason": "x"}, {"latitude": "91", "longitude": "73", "reason": "x"}):
            self.assertEqual(self.post_request(**payload).status_code, 400)
        self.shop.latitude, self.shop.longitude = Decimal("18.520430"), Decimal("73.856744")
        self.shop.save(update_fields=["latitude", "longitude"])
        self.assertEqual(self.post_request().status_code, 400)

    def test_super_admin_approval_updates_both_coordinates_and_audits(self):
        created = self.post_request()
        self.client.force_authenticate(self.super_admin)
        response = self.client.post(reverse("super-admin-location-request-review", args=[created.data["id"]]), {"decision": "approve"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.latitude, Decimal("18.520430"))
        self.assertEqual(self.shop.longitude, Decimal("73.856744"))
        self.assertTrue(SuperAdminAuditLog.objects.filter(action="shop_location_request_approved", resource_id=created.data["id"]).exists())

    def test_request_and_rejection_accept_omitted_reasons(self):
        self.client.force_authenticate(self.owner)
        created = self.client.post(
            reverse("shop-admin-location-requests"),
            {"latitude": "18.520430", "longitude": "73.856744"},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["reason"], "")
        self.client.force_authenticate(self.super_admin)
        reviewed = self.client.post(
            reverse("super-admin-location-request-review", args=[created.data["id"]]),
            {"decision": "reject"},
            format="json",
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.data["review_reason"], None)
        self.assertTrue(SuperAdminAuditLog.objects.filter(action="shop_location_request_rejected", resource_id=created.data["id"]).exists())

    def test_rejection_and_stale_request_do_not_change_approved_location(self):
        created = self.post_request()
        self.client.force_authenticate(self.super_admin)
        rejected = self.client.post(reverse("super-admin-location-request-review", args=[created.data["id"]]), {"decision": "reject", "reason": "Does not match premises."}, format="json")
        self.assertEqual(rejected.status_code, 200)
        self.shop.refresh_from_db(); self.assertIsNone(self.shop.latitude)
        second = self.post_request(latitude="18.520430", longitude="73.856744", reason="Retry.")
        self.shop.latitude, self.shop.longitude = Decimal("19.000000"), Decimal("72.000000")
        self.shop.save(update_fields=["latitude", "longitude"])
        self.client.force_authenticate(self.super_admin)
        stale = self.client.post(reverse("super-admin-location-request-review", args=[second.data["id"]]), {"decision": "approve", "reason": "Review."}, format="json")
        self.assertEqual(stale.status_code, 409)

    def test_only_super_admin_can_review_and_review_reason_is_required(self):
        created = self.post_request()
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(reverse("super-admin-location-request-review", args=[created.data["id"]]), {"decision": "approve", "reason": "x"}, format="json").status_code, 403)
        self.client.force_authenticate(self.super_admin)
        self.assertEqual(self.client.post(reverse("super-admin-location-request-review", args=[created.data["id"]]), {"decision": "approve"}, format="json").status_code, 200)

    def test_second_review_is_conflict_and_cannot_apply_twice(self):
        created = self.post_request()
        self.client.force_authenticate(self.super_admin)
        url = reverse("super-admin-location-request-review", args=[created.data["id"]])
        self.assertEqual(self.client.post(url, {"decision": "approve", "reason": "Verified."}, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, {"decision": "approve", "reason": "Retry."}, format="json").status_code, 409)
