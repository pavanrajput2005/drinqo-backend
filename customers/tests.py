from datetime import date, timedelta
from datetime import datetime
from decimal import Decimal
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import (
    AccountDeletionRequest,
    Cart,
    CartItem,
    CustomerProfile,
    CustomerVerificationSession,
    CustomerVehicle,
    LegalPolicy,
    Notification,
    SupportTicket,
)
from products.models import Product
from orders.models import Order
from shops.models import Shop, ShopLicenseDocument
from shop_admin.models import SuperAdminAuditLog


class CustomerAdditionalApiTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.user = User.objects.create_user(
            username="api-customer",
            email="api-customer@example.com",
            password="customer-password",
            user_type="customer",
        )
        self.other_user = User.objects.create_user(
            username="api-other-customer",
            email="api-other-customer@example.com",
            password="customer-password",
            user_type="customer",
        )
        self.shop_admin = User.objects.create_user(
            username="api-shop-admin",
            email="api-shop-admin@example.com",
            password="StrongTestPassword#123",
            user_type="shop_admin",
        )
        self.admin = User.objects.create_user(
            username="api-super-admin",
            email="api-super-admin@example.com",
            password="admin-password",
            user_type="super_admin",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_customer_only_endpoints_reject_shop_admin_and_super_admin(self):
        customer_only_endpoints = (
            reverse("profile"),
            reverse("age_verification"),
            reverse("customer-profile"),
            reverse("customer-vehicle-list-create"),
            reverse("customer-vehicle-detail", args=[999]),
            reverse("favorite-shop-list-create"),
            reverse("favorite-shop-delete", args=[999]),
            reverse("customer-cart"),
            reverse("cart-item-add"),
            reverse("cart-item-update", args=[999]),
            reverse("cart-item-delete", args=[999]),
            reverse("checkout-preview"),
            reverse("notification-list"),
            reverse("notification-read", args=[999]),
            reverse("notification-read-all"),
            reverse("notification-preferences"),
            reverse("customer-push-token"),
            reverse("customer-verification-sessions"),
            reverse("customer-verification-session-detail", args=[999]),
            reverse("support-ticket-list-create"),
            reverse("support-ticket-detail", args=[999]),
            reverse("account-deletion-request"),
            reverse("account-deletion-request-cancel"),
            reverse("order-create"),
            reverse("order-history"),
            reverse("order-detail", args=[999]),
            reverse("payment-status", args=[999]),
            reverse("order-cancel", args=[999]),
            reverse("order-tracking", args=[999]),
        )

        for user in (self.shop_admin, self.admin):
            self.client.force_authenticate(user)
            for endpoint in customer_only_endpoints:
                with self.subTest(role=user.user_type, endpoint=endpoint):
                    response = self.client.get(endpoint)
                    self.assertEqual(response.status_code, 403)

    def test_customer_legal_policy_and_identity_reads_remain_shared(self):
        for user in (self.user, self.shop_admin, self.admin):
            self.client.force_authenticate(user)
            with self.subTest(role=user.user_type):
                self.assertEqual(
                    self.client.get(reverse("customer-legal-policies")).status_code,
                    200,
                )
                self.assertEqual(self.client.get(reverse("me")).status_code, 200)

    def test_customer_profile_and_vehicle_changes_persist(self):
        profile_url = reverse("customer-profile")
        response = self.client.patch(
            profile_url,
            {
                "full_name": "Customer Name",
                "date_of_birth": "2000-01-01",
                "phone": "+919876543210",
                "address_line1": "1 Test Road",
                "city": "Pune",
                "state": "Maharashtra",
                "postal_code": "411001",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        profile = CustomerProfile.objects.get(user=self.user)
        self.assertEqual(profile.full_name, "Customer Name")
        self.user.refresh_from_db()
        self.assertEqual(self.user.date_of_birth, date(2000, 1, 1))
        self.assertFalse(self.user.age_self_declared)
        self.assertIsNone(self.user.age_self_declared_at)
        self.assertIsNotNone(profile.profile_completed_at)
        self.assertEqual(
            self.client.get(profile_url).data["address_line1"],
            "1 Test Road",
        )

        vehicles_url = reverse("customer-vehicle-list-create")
        response = self.client.post(
            vehicles_url,
            {
                "vehicle_type": "car",
                "vehicle_number": "MH12AB1234",
                "vehicle_name": "Daily Car",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        vehicle_id = response.data["id"]
        vehicle_url = reverse("customer-vehicle-detail", args=[vehicle_id])

        response = self.client.patch(
            vehicle_url,
            {"vehicle_name": "Updated Car"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get(vehicles_url).data["results"][0]["vehicle_name"],
            "Updated Car",
        )

        response = self.client.delete(vehicle_url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get(vehicles_url).data["results"], [])

    def test_vehicle_api_accepts_each_canonical_vehicle_type_and_rejects_unknown_type(self):
        vehicles_url = reverse("customer-vehicle-list-create")
        for index, vehicle_type in enumerate(("car", "bike", "scooter", "other")):
            with self.subTest(vehicle_type=vehicle_type):
                response = self.client.post(
                    vehicles_url,
                    {
                        "vehicle_type": vehicle_type,
                        "vehicle_number": f"MH12AA{1000 + index}",
                        "vehicle_name": f"Test {vehicle_type}",
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["vehicle_type"], vehicle_type)

        invalid = self.client.post(
            vehicles_url,
            {
                "vehicle_type": "Car",
                "vehicle_number": "MH12AA1999",
                "vehicle_name": "Invalid enum",
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("vehicle_type", invalid.data)

    def test_customer_can_submit_and_read_private_verification_status(self):
        response = self.client.post(
            reverse("customer-verification-sessions"),
            {
                "document_type": "government_id",
                "document": SimpleUploadedFile(
                    "identity.pdf",
                    b"%PDF-1.4 private test document",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "pending")
        self.assertNotIn("document", response.data)
        session = CustomerVerificationSession.objects.get(user=self.user)

        response = self.client.get(
            reverse("customer-verification-session-detail", args=[session.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "pending")

        self.client.force_authenticate(self.other_user)
        response = self.client.get(
            reverse("customer-verification-session-detail", args=[session.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_customer_support_ticket_is_private_and_status_is_read_only(self):
        response = self.client.post(
            reverse("support-ticket-list-create"),
            {
                "subject": "Need help",
                "message": "Please help with my order.",
                "status": "resolved",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "open")
        ticket_id = response.data["id"]
        self.client.force_authenticate(self.other_user)
        response = self.client.get(
            reverse("support-ticket-detail", args=[ticket_id])
        )
        self.assertEqual(response.status_code, 404)

    def test_account_deletion_request_can_be_cancelled_during_grace_period(self):
        response = self.client.post(reverse("account-deletion-request"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "pending")
        self.assertGreaterEqual(
            datetime.fromisoformat(response.data["scheduled_for"].replace("Z", "+00:00")),
            timezone.now() + timedelta(days=29),
        )
        response = self.client.post(
            reverse("account-deletion-request-cancel")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "cancelled")

    def test_notification_preferences_read_and_update_and_mark_all_read(self):
        Notification.objects.create(
            user=self.user,
            title="First",
            message="One",
        )
        Notification.objects.create(
            user=self.user,
            title="Second",
            message="Two",
        )
        response = self.client.patch(
            reverse("notification-preferences"),
            {"promotions_enabled": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["promotions_enabled"])

        response = self.client.post(reverse("notification-read-all"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated_count"], 2)

    def test_push_token_registration_and_removal(self):
        payload = {
            "token": "test-fcm-device-token",
            "platform": "android",
        }
        response = self.client.post(
            reverse("customer-push-token"),
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        response = self.client.delete(
            reverse("customer-push-token"),
            {"token": payload["token"]},
            format="json",
        )
        self.assertEqual(response.status_code, 204)

    def test_only_published_legal_policies_are_visible_to_customers(self):
        LegalPolicy.objects.create(
            slug="terms",
            title="Terms",
            version="1",
            content="Published terms",
            is_published=True,
            updated_by=self.admin,
        )
        LegalPolicy.objects.create(
            slug="draft",
            title="Draft",
            version="1",
            content="Draft text",
            is_published=False,
            updated_by=self.admin,
        )
        response = self.client.get(reverse("customer-legal-policies"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["slug"], "terms")

    def test_account_deletion_cancel_after_grace_deadline_is_rejected(self):
        deletion_request = AccountDeletionRequest.objects.create(
            user=self.user,
            scheduled_for=timezone.now() - timedelta(seconds=1),
        )
        response = self.client.post(
            reverse("account-deletion-request-cancel")
        )
        self.assertEqual(response.status_code, 400)
        deletion_request.refresh_from_db()
        self.assertEqual(deletion_request.status, "pending")


class CustomerCheckoutOrderContractTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="checkout-customer",
            email="checkout-customer@example.com",
            phone="9876543210",
            password="test-checkout-password",
            user_type="customer",
            age_verified=True,
            date_of_birth=date(2000, 1, 1),
            age_verified_at=timezone.now(),
        )
        self.shop_admin = User.objects.create_user(
            username="checkout-shop-admin",
            email="checkout-shop-admin@example.com",
            password="test-checkout-password",
            user_type="shop_admin",
        )
        self.age_verification_reviewer = User.objects.create_user(
            username="age-verification-reviewer",
            email="age-verification-reviewer@example.com",
            password=None,
            user_type="super_admin",
        )
        CustomerVerificationSession.objects.create(
            user=self.customer,
            document="private_customer_verification/checkout-age-proof.pdf",
            document_type="age_proof",
            status="approved",
            reviewed_by=self.age_verification_reviewer,
            reviewed_at=timezone.now(),
        )
        SuperAdminAuditLog.objects.create(
            actor=self.age_verification_reviewer,
            actor_email=self.age_verification_reviewer.email,
            action="customer_document_approved",
            resource_type="verification_session",
            resource_id=CustomerVerificationSession.objects.get(
                user=self.customer,
                document_type="age_proof",
            ).id,
            summary="Approved customer age proof.",
            metadata={
                "age_proof": True,
                "confirmed_date_of_birth": "2000-01-01",
                "age_verified": True,
            },
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Checkout Shop",
            phone="9876543210",
            address_line1="1 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="CHECKOUT-LICENSE-1",
            license_status="verified",
            is_active=True,
            latitude=Decimal("18.520430"),
            longitude=Decimal("73.856744"),
        )
        ShopLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/checkout-license.pdf",
            expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status="verified",
            verified_at=timezone.now(),
        )
        self.profile = CustomerProfile.objects.create(
            user=self.customer,
            full_name="Checkout Customer",
            address_line1="2 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411002",
        )
        self.cart = Cart.objects.create(customer=self.profile)
        self.vehicle = CustomerVehicle.objects.create(
            customer=self.profile,
            vehicle_type="car",
            vehicle_number="MH12ZZ1234",
            vehicle_name="Checkout car",
        )
        self.product = Product.objects.create(
            shop=self.shop,
            name="Checkout Product",
            category="other",
            price=Decimal("499.99"),
            stock_quantity=5,
        )
        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            quantity=1,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.customer)

    def test_cart_add_rejects_invalid_product_ids_and_quantities(self):
        invalid_product_ids = (
            True,
            False,
            0,
            -1,
            "abc",
            "1.5",
            [],
        )
        for product_id in invalid_product_ids:
            with self.subTest(product_id=product_id):
                response = self.client.post(
                    reverse("cart-item-add"),
                    {"product_id": product_id, "quantity": 1},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("product_id", response.data)

        invalid_quantities = (True, False, 0, -1, "abc", "1.5", [])
        for quantity in invalid_quantities:
            with self.subTest(quantity=quantity):
                response = self.client.post(
                    reverse("cart-item-add"),
                    {"product_id": self.product.id, "quantity": quantity},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("quantity", response.data)

    def test_cart_quantity_update_rejects_non_positive_or_non_integer_values(self):
        for quantity in (True, False, 0, -1, "abc", "1.5", []):
            with self.subTest(quantity=quantity):
                response = self.client.patch(
                    reverse("cart-item-update", args=[self.cart_item.id]),
                    {"quantity": quantity},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("quantity", response.data)

    def test_checkout_rejects_non_finite_and_non_numeric_coordinates(self):
        for field, value in (
            ("latitude", "NaN"),
            ("latitude", "Infinity"),
            ("latitude", "91"),
            ("latitude", "1e10000"),
            ("latitude", True),
            ("longitude", "-Infinity"),
            ("longitude", "-181"),
            ("longitude", "not-a-number"),
            ("longitude", False),
        ):
            with self.subTest(field=field, value=value):
                payload = {
                    "pickup_mode": "vehicle",
                    "vehicle_id": self.vehicle.id,
                    "latitude": "18.520430",
                    "longitude": "73.856744",
                }
                payload[field] = value
                response = self.client.post(
                    reverse("checkout-preview"),
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data["errors"])

    def test_preview_returns_calculated_amounts_and_minimum_eligibility(self):
        response = self.client.post(
            reverse("checkout-preview"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
                "subtotal": "1.00",
                "total_amount": "1.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["can_checkout"])
        self.assertEqual(response.data["subtotal"], "499.99")
        self.assertEqual(response.data["application_fee"], "50.00")
        self.assertEqual(response.data["total_amount"], "549.99")
        self.assertEqual(response.data["minimum_order_amount"], "500.00")
        self.assertIn("₹500", response.data["detail"])

    def test_preview_requires_jwt_and_customer_role(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            reverse("checkout-preview"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer not-a-django-jwt",
        )
        self.assertEqual(response.status_code, 401)

        self.client.force_authenticate(self.shop_admin)
        response = self.client.post(
            reverse("checkout-preview"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_preview_and_order_create_accept_minimum_product_subtotal(self):
        self.product.price = Decimal("500.00")
        self.product.save(update_fields=["price"])
        preview = self.client.post(
            reverse("checkout-preview"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
            },
            format="json",
        )

        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data["can_checkout"])
        self.assertEqual(preview.data["subtotal"], "500.00")
        self.assertEqual(preview.data["application_fee"], "50.00")
        self.assertEqual(preview.data["total_amount"], "550.00")

        created = self.client.post(
            reverse("order-create"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
                "subtotal": "1.00",
                "total_amount": "1.00",
            },
            format="json",
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["subtotal"], "500.00")
        self.assertEqual(created.data["application_fee"], "50.00")
        self.assertEqual(created.data["total_amount"], "550.00")
        self.assertIn("order_number", created.data)
        self.assertEqual(
            set(created.data["items"][0]),
            {"id", "product", "product_name", "unit_price", "quantity", "subtotal"},
        )
        self.assertEqual(created.data["vehicle_details"]["id"], self.vehicle.id)

    def test_persisted_age_self_declaration_allows_preview_and_order_without_proof(self):
        self.customer.age_verified = False
        self.customer.age_verified_at = None
        self.customer.age_self_declared = True
        self.customer.age_self_declared_at = timezone.now()
        self.customer.save(update_fields=[
            "age_verified", "age_verified_at", "age_self_declared", "age_self_declared_at"
        ])
        self.product.price = Decimal("500.00")
        self.product.save(update_fields=["price"])
        payload = {
            "pickup_mode": "vehicle",
            "vehicle_id": self.vehicle.id,
            "latitude": "18.520430",
            "longitude": "73.856744",
        }
        preview = self.client.post(reverse("checkout-preview"), payload, format="json")
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data["can_checkout"])
        created = self.client.post(reverse("order-create"), payload, format="json")
        self.assertEqual(created.status_code, 201)

    def test_preview_and_order_return_specific_eligibility_codes(self):
        payload = {
            "pickup_mode": "vehicle",
            "vehicle_id": self.vehicle.id,
            "latitude": "18.520430",
            "longitude": "73.856744",
        }
        cases = (
            ("profile_incomplete", {"full_name": ""}, 400),
            ("date_of_birth_required", {"date_of_birth": None, "age_verified": False}, 403),
            ("underage_customer", {"date_of_birth": timezone.localdate().replace(year=timezone.localdate().year - 20), "age_verified": False}, 403),
            ("age_self_declaration_required", {"age_verified": False, "age_self_declared": False, "age_self_declared_at": None}, 403),
        )
        for code, updates, expected_status in cases:
            with self.subTest(code=code):
                self.customer.age_verified = True
                self.customer.age_self_declared = True
                self.customer.age_self_declared_at = timezone.now()
                self.customer.date_of_birth = date(2000, 1, 1)
                self.customer.save(update_fields=[
                    "age_verified", "age_self_declared", "age_self_declared_at", "date_of_birth"
                ])
                self.profile.full_name = "Checkout Customer"
                self.profile.save(update_fields=["full_name"])
                profile_fields = {"full_name", "phone", "address_line1", "city", "state", "postal_code"}
                for field, value in updates.items():
                    if field in profile_fields:
                        setattr(self.profile, field, value)
                    else:
                        setattr(self.customer, field, value)
                self.customer.save()
                self.profile.save()
                for url_name in ("checkout-preview", "order-create"):
                    response = self.client.post(reverse(url_name), payload, format="json")
                    self.assertEqual(response.status_code, expected_status)
                    self.assertEqual(response.data["code"], code)

    def test_application_fee_counts_toward_high_value_order_total(self):
        self.product.price = Decimal("14950.00")
        self.product.save(update_fields=["price"])

        preview = self.client.post(
            reverse("checkout-preview"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data["subtotal"], "14950.00")
        self.assertEqual(preview.data["application_fee"], "50.00")
        self.assertEqual(preview.data["total_amount"], "15000.00")

        created = self.client.post(
            reverse("order-create"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["status"], "pending_super_admin")
        self.assertEqual(created.data["subtotal"], "14950.00")
        self.assertEqual(created.data["total_amount"], "15000.00")

    def test_order_create_rejects_subtotal_below_minimum(self):
        response = self.client.post(
            reverse("order-create"),
            {
                "pickup_mode": "vehicle",
                "vehicle_id": self.vehicle.id,
                "latitude": "18.520430",
                "longitude": "73.856744",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["minimum_order_amount"], Decimal("500.00"))

    def test_preview_and_order_create_enforce_shop_radius(self):
        self.product.price = Decimal("1000.00")
        self.product.save(update_fields=["price"])
        payload = {
            "pickup_mode": "vehicle",
            "vehicle_id": self.vehicle.id,
            "latitude": "18.521330",
            "longitude": "73.856744",
        }

        preview = self.client.post(
            reverse("checkout-preview"),
            payload,
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data["can_checkout"])
        self.assertGreater(preview.data["distance_to_shop_m"], 100)
        self.assertEqual(preview.data["pickup_radius_m"], 100)
        self.assertIn("100 meters", preview.data["detail"])

        created = self.client.post(
            reverse("order-create"),
            payload,
            format="json",
        )
        self.assertEqual(created.status_code, 400)
        self.assertIn("100 meters", created.data["detail"])

    def test_closed_shop_preserves_cart_and_blocks_new_checkout(self):
        self.product.price = Decimal("1000.00")
        self.product.save(update_fields=["price"])
        self.shop.is_open = False
        self.shop.save(update_fields=["is_open"])
        payload = {
            "pickup_mode": "vehicle",
            "vehicle_id": self.vehicle.id,
            "latitude": "18.520430",
            "longitude": "73.856744",
        }

        add = self.client.post(
            reverse("cart-item-add"),
            {"product_id": self.product.id, "quantity": 1},
            format="json",
        )
        update = self.client.patch(
            reverse("cart-item-update", args=[self.cart_item.id]),
            {"quantity": 2},
            format="json",
        )
        preview = self.client.post(
            reverse("checkout-preview"),
            payload,
            format="json",
        )
        created = self.client.post(
            reverse("order-create"),
            payload,
            format="json",
        )

        self.assertEqual(add.status_code, 404)
        self.assertEqual(update.status_code, 400)
        self.assertEqual(preview.status_code, 400)
        self.assertEqual(created.status_code, 400)
        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.quantity, 1)
        self.assertEqual(self.cart.items.count(), 1)
        self.assertEqual(Order.objects.count(), 0)

        self.shop.is_open = True
        self.shop.save(update_fields=["is_open"])
        preview = self.client.post(
            reverse("checkout-preview"),
            payload,
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data["can_checkout"])
