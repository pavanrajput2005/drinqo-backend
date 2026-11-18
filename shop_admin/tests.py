from io import BytesIO
from datetime import date
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image

from customers.models import Notification
from orders.models import Order, OrderItem
from orders.testing_payments import record_captured_payment_for_test
from products.models import StockMovement
from shops.models import Shop, ShopLicenseDocument as ApplicationLicenseDocument

from .models import ShopAdminProfile, SuperAdminAuditLog
from .serializers import ShopAdminProfileSerializer


class ShopAdminProfileEmailTests(TestCase):
    def test_shop_admin_profile_email_is_saved_and_serialized(self):
        user = get_user_model().objects.create_user(
            username="shopadmin",
            email="shopadmin@example.com",
            password="pass1234",
            user_type="shop_admin",
        )

        profile = ShopAdminProfile.objects.create(
            user=user,
            shop_name="Drinqo Wines",
            phone="9876543210",
            email="shop@drinqo.com",
        )

        self.assertEqual(profile.email, "shop@drinqo.com")

        serializer = ShopAdminProfileSerializer(instance=profile)
        self.assertEqual(serializer.data["email"], "shop@drinqo.com")


class ShopAvailabilityEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.shop_admin = self.user_model.objects.create_user(
            username="shopadmin1",
            email="shopadmin1@example.com",
            password="pass1234",
            firebase_uid="firebase-shop-admin-1",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="DRINQO Test Shop",
            phone="9876543210",
            email="shop@example.com",
            address_line1="22 Main Road",
            address_line2="",
            city="Pune",
            state="MH",
            postal_code="411001",
            license_number="LIC-1001",
            license_status="verified",
            is_active=True,
        )
        ApplicationLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/availability-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )

    def test_shop_availability_updates_assigned_shop(self):
        self.client.force_authenticate(self.shop_admin)

        response = self.client.patch(
            "/api/shop-admin/shop/availability/",
            {"is_active": False},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer valid-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "Shop availability updated successfully.")
        self.assertEqual(response.json()["shop"]["shop_name"], "DRINQO Test Shop")
        self.assertFalse(response.json()["shop"]["is_active"])

        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_active)

    def test_shop_availability_rejects_invalid_boolean(self):
        self.client.force_authenticate(self.shop_admin)

        response = self.client.patch(
            "/api/shop-admin/shop/availability/",
            {"is_active": "yes"},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer valid-token",
        )

        self.assertEqual(response.status_code, 400)

    def test_shop_availability_requires_assigned_shop(self):
        user = self.user_model.objects.create_user(
            username="shopadmin-no-shop",
            email="shopadmin-no-shop@example.com",
            password="pass1234",
            firebase_uid="firebase-shop-admin-2",
            user_type="shop_admin",
        )
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/shop-admin/shop/availability/",
            {"is_active": False},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer valid-token",
        )

        self.assertEqual(response.status_code, 403)

    def test_shop_availability_requires_shop_admin_role(self):
        customer = self.user_model.objects.create_user(
            username="customer1",
            email="customer1@example.com",
            password="pass1234",
            firebase_uid="firebase-customer-1",
            user_type="customer",
        )
        self.shop.owner = customer
        self.shop.save(update_fields=["owner"])

        self.client.force_authenticate(customer)

        response = self.client.patch(
            "/api/shop-admin/shop/availability/",
            {"is_active": True},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer valid-token",
        )

        self.assertEqual(response.status_code, 403)


class ShopLocationUpdateTests(TestCase):
    def setUp(self):
        self.shop_admin = get_user_model().objects.create_user(
            username="location-shop-admin",
            email="location-shop-admin@example.com",
            password="LocationPassword#123",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Location Test Shop",
            phone="9876543210",
            email="location@example.com",
            address_line1="1 Location Road",
            city="Pune",
            state="MH",
            postal_code="411001",
            license_number="LOCATION-TEST-LICENSE",
            license_status="verified",
            is_active=True,
        )
        ApplicationLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/location-test.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.shop_admin)

    def test_shop_admin_profile_update_cannot_write_coordinates(self):
        response = self.client.patch(
            reverse("shop_admin_update_shop"),
            {"latitude": "18.520430", "longitude": "73.856744"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["fields"], ["latitude", "longitude"])
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.latitude)
        self.assertIsNone(self.shop.longitude)

    def test_shop_admin_profile_update_rejects_coordinate_fields(self):
        for payload in (
            {"latitude": "18.520430"},
            {"longitude": "73.856744"},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(
                    reverse("shop_admin_update_shop"),
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.latitude)
        self.assertIsNone(self.shop.longitude)

    def test_shop_admin_location_update_rejects_unsupported_fields(self):
        response = self.client.patch(
            reverse("shop_admin_update_shop"),
            {"latitude": "18.520430", "is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("is_active", response.data["fields"])
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.latitude)

    def test_shop_admin_profile_update_is_scoped_to_authenticated_owner(self):
        other_admin = get_user_model().objects.create_user(
            username="other-location-admin",
            email="other-location-admin@example.com",
            password="LocationPassword#123",
            user_type="shop_admin",
        )
        other_shop = Shop.objects.create(
            owner=other_admin,
            shop_name="Other Location Shop",
            phone="9876543211",
            email="other-location@example.com",
            address_line1="2 Location Road",
            city="Pune",
            state="MH",
            postal_code="411002",
            license_number="OTHER-LOCATION-TEST-LICENSE",
            license_status="verified",
            is_active=True,
        )
        ApplicationLicenseDocument.objects.create(
            shop=other_shop,
            license_number=other_shop.license_number,
            document="private_shop_licenses/other-location-test.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )

        self.client.force_authenticate(other_admin)
        response = self.client.patch(
            reverse("shop_admin_update_shop"),
            {"phone": "9876543222"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        other_shop.refresh_from_db()
        self.shop.refresh_from_db()
        self.assertEqual(other_shop.phone, "9876543222")
        self.assertIsNone(self.shop.latitude)
        self.assertIsNone(self.shop.longitude)


class ShopLicenseRenewalUploadTests(TestCase):
    def setUp(self):
        media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(media_dir.cleanup)
        settings_override = override_settings(MEDIA_ROOT=media_dir.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.shop_admin = get_user_model().objects.create_user(
            username="license-renewal-admin",
            email="license-renewal-admin@example.com",
            password="LicensePassword#123",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="License Renewal Shop",
            phone="9876543212",
            email="renewal@example.com",
            address_line1="3 License Road",
            city="Pune",
            state="MH",
            postal_code="411003",
            license_number="LICENSE-RENEWAL-TEST",
            license_status="verified",
            is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.shop_admin)

    def test_shop_admin_can_upload_pending_license_for_owned_approved_shop(self):
        response = self.client.post(
            reverse("shop-admin-license-document-upload"),
            {
                "license_number": self.shop.license_number,
                "expiry_date": "2030-12-31",
                "license_document": SimpleUploadedFile(
                    "renewal.pdf",
                    b"%PDF-1.4 test license",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["verification_status"], "pending")
        self.assertEqual(
            ApplicationLicenseDocument.objects.filter(
                shop=self.shop,
                verification_status="pending",
            ).count(),
            1,
        )
        self.assertTrue(
            SuperAdminAuditLog.objects.filter(
                action="shop_license_renewal_submitted",
                resource_id=self.shop.id,
            ).exists()
        )
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.license_status, "verified")
        self.assertTrue(self.shop.is_active)

        customer = get_user_model().objects.create_user(
            username="license-renewal-customer",
            email="license-renewal-customer@example.com",
            password="CustomerPassword#123",
            user_type="customer",
        )
        customer_client = APIClient()
        customer_client.force_authenticate(customer)
        discovery = customer_client.get(reverse("customer-shop-list"))
        self.assertEqual(discovery.status_code, 200)
        self.assertFalse(
            any(item["id"] == self.shop.id for item in discovery.data["results"])
        )


class ShopAdminInventoryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_model = get_user_model()
        self.shop_admin = self.user_model.objects.create_user(
            username="inventory-admin",
            email="inventory-admin@example.com",
            password="pass1234",
            firebase_uid="firebase-inventory-admin",
            user_type="shop_admin",
        )
        self.other_admin = self.user_model.objects.create_user(
            username="other-admin",
            email="other-admin@example.com",
            password="pass1234",
            firebase_uid="firebase-other-admin",
            user_type="shop_admin",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_admin,
            shop_name="Inventory Shop",
            phone="9876543211",
            email="inventory@example.com",
            address_line1="11 Market Road",
            city="Pune",
            state="MH",
            postal_code="411002",
            license_number="LIC-INV-1",
            is_active=True,
            license_status="verified",
        )
        ApplicationLicenseDocument.objects.create(
            shop=self.shop,
            license_number=self.shop.license_number,
            document="private_shop_licenses/inventory-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )
        self.other_shop = Shop.objects.create(
            owner=self.other_admin,
            shop_name="Other Inventory Shop",
            phone="9876543212",
            email="other@example.com",
            address_line1="22 Market Road",
            city="Pune",
            state="MH",
            postal_code="411003",
            license_number="LIC-INV-2",
            is_active=True,
        )
        self.product = self.shop.products.create(
            name="Premium Whisky",
            brand="Local",
            category="whisky",
            description="Bottle",
            price="499.00",
            stock_quantity=10,
            is_active=True,
        )
        self.other_product = self.other_shop.products.create(
            name="Other Whisky",
            brand="Local",
            category="whisky",
            description="Bottle",
            price="599.00",
            stock_quantity=5,
            is_active=True,
        )
        self.client.force_authenticate(user=self.shop_admin)

    def _create_reserved_order(self):
        customer = self.user_model.objects.create_user(
            username="customer-rejected-order",
            email="customer-rejected-order@example.com",
            password="test-password",
            user_type="customer",
        )
        self.product.price = "1000.00"
        self.product.stock_quantity = 9
        self.product.save(
            update_fields=["price", "stock_quantity", "updated_at"]
        )
        order = Order.objects.create(
            customer=customer,
            shop=self.shop,
            status="pending",
            subtotal="1000.00",
            application_fee="50.00",
            total_amount="1050.00",
            payment_method="online",
            payment_status="pending",
            pickup_mode="vehicle",
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=1,
            subtotal="1000.00",
        )
        return order, customer

    def _reject_order(self, order):
        return self.client.post(
            reverse("shop-admin-order-decision", args=[order.id]),
            {"decision": "rejected", "reason_code": "out_of_stock"},
            format="json",
        )

    def test_customer_cannot_cancel_shop_admin_rejected_order(self):
        order, customer = self._create_reserved_order()

        rejected = self._reject_order(order)

        self.assertEqual(rejected.status_code, 200)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "rejected")
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertEqual(
            StockMovement.objects.filter(
                product=self.product,
                note=f"Stock returned after order #{order.id} was rejected",
            ).count(),
            1,
        )

        customer_client = APIClient()
        customer_client.force_authenticate(customer)
        cancellation = customer_client.post(
            reverse("order-cancel", args=[order.id])
        )

        self.assertEqual(cancellation.status_code, 409)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "rejected")
        self.assertEqual(self.product.stock_quantity, 10)

    def test_repeated_rejection_and_cancellation_do_not_restore_stock_again(self):
        order, customer = self._create_reserved_order()
        initial_rejection = self._reject_order(order)
        self.assertEqual(initial_rejection.status_code, 200)

        repeated_rejection = self._reject_order(order)
        customer_client = APIClient()
        customer_client.force_authenticate(customer)
        first_cancellation = customer_client.post(
            reverse("order-cancel", args=[order.id])
        )
        repeated_cancellation = customer_client.post(
            reverse("order-cancel", args=[order.id])
        )

        self.assertEqual(repeated_rejection.status_code, 409)
        self.assertEqual(first_cancellation.status_code, 409)
        self.assertEqual(repeated_cancellation.status_code, 409)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "rejected")
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertEqual(
            StockMovement.objects.filter(
                product=self.product,
                note=f"Stock returned after order #{order.id} was rejected",
            ).count(),
            1,
        )

    def test_get_inventory_returns_only_assigned_shop_products(self):
        response = self.client.get("/api/shop-admin/inventory/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], self.product.id)
        self.assertEqual(response.data[0]["stock_quantity"], 10)

    def test_accept_vehicle_order_uses_exact_customer_message(self):
        customer = self.user_model.objects.create_user(
            username="customer-order",
            email="customer-order@example.com",
            password="pass1234",
            firebase_uid="firebase-customer-order",
            user_type="customer",
        )

        order = Order.objects.create(
            customer=customer,
            shop=self.shop,
            status="pending",
            subtotal="100.00",
            total_amount="150.00",
            payment_method="online",
            payment_status="pending",
            pickup_mode="vehicle",
        )

        response = self.client.post(
            f"/api/shop-admin/orders/{order.id}/accept/",
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["message"],
            "Your order has been accepted. Our shop staff will hand over your order at your vehicle in approximately 10 minutes.",
        )

    def _assert_acceptance_flow(self, endpoint, approved=False):
        order, customer = self._create_reserved_order()
        if approved:
            order.status = "admin_approved"
            order.subtotal = "14950.00"
            order.total_amount = "15000.00"
            order.super_admin_approved_at = timezone.now()
            order.save()
        payload = {"decision": "accepted"} if endpoint == "decision" else {}
        response = self.client.post(
            f"/api/shop-admin/orders/{order.id}/{endpoint}/",
            payload, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "confirmed")
        if endpoint == "decision":
            self.assertEqual(response.data["decision"], "accepted")
            self.assertTrue(response.data["notification_created"])
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(order.decided_by_id, self.shop_admin.id)
        self.assertIsNotNone(order.decided_at)
        notification = Notification.objects.get(order=order, user=customer)
        self.assertEqual(notification.title, "Order Accepted!")
        self.assertEqual(
            notification.message,
            "Your order has been accepted. Our shop staff will hand over your order at your vehicle in approximately 10 minutes.",
        )
        self.assertEqual(
            response.data["customer_message" if endpoint == "decision" else "message"],
            notification.message,
        )
        decided_at = order.decided_at
        for retry_endpoint, expected_code in (("decision", 409), ("accept", 400)):
            retry = self.client.post(
                f"/api/shop-admin/orders/{order.id}/{retry_endpoint}/",
                {"decision": "accepted"} if retry_endpoint == "decision" else {},
                format="json",
            )
            self.assertEqual(retry.status_code, expected_code)
        self.assertEqual(Notification.objects.filter(order=order).count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.decided_at, decided_at)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 9)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())
        skipped = self.client.post(
            f"/api/shop-admin/orders/{order.id}/status/",
            {"status": "ready"}, format="json",
        )
        self.assertEqual(skipped.status_code, 400)
        # Acceptance stays unpaid; preparation assumes a subsequent verified capture.
        record_captured_payment_for_test(order)
        for next_status in ("preparing", "ready"):
            updated = self.client.post(
                f"/api/shop-admin/orders/{order.id}/status/",
                {"status": next_status}, format="json",
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.data["status"], next_status)
        self.assertEqual(
            Notification.objects.filter(order=order, title="Order Accepted!").count(), 1,
        )

    def test_decision_acceptance_and_cross_endpoint_retries(self):
        self._assert_acceptance_flow("decision")

    def test_accept_acceptance_and_cross_endpoint_retries(self):
        self._assert_acceptance_flow("accept")

    def test_decision_accepts_super_admin_approved_high_value_order(self):
        self._assert_acceptance_flow("decision", approved=True)

    def test_accept_accepts_super_admin_approved_high_value_order(self):
        self._assert_acceptance_flow("accept", approved=True)

    def test_both_acceptance_endpoints_block_unapproved_high_value_order(self):
        order, customer = self._create_reserved_order()
        order.subtotal = "14950.00"
        order.total_amount = "15000.00"
        order.save()
        for endpoint, expected_code in (("decision", 409), ("accept", 403)):
            response = self.client.post(
                f"/api/shop-admin/orders/{order.id}/{endpoint}/",
                {"decision": "accepted"} if endpoint == "decision" else {},
                format="json",
            )
            self.assertEqual(response.status_code, expected_code)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.assertIsNone(order.decided_at)
        self.assertFalse(Notification.objects.filter(order=order).exists())

    def test_both_acceptance_endpoints_reject_foreign_shop_items(self):
        order, customer = self._create_reserved_order()
        order.items.update(product=self.other_product)
        for endpoint in ("decision", "accept"):
            response = self.client.post(
                f"/api/shop-admin/orders/{order.id}/{endpoint}/",
                {"decision": "accepted"} if endpoint == "decision" else {},
                format="json",
            )
            self.assertEqual(response.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.assertFalse(Notification.objects.filter(order=order).exists())

    def test_adjust_inventory_requires_quantity_delta_and_updates_stock(self):
        response = self.client.post(
            f"/api/shop-admin/inventory/{self.product.id}/adjust/",
            {"quantity_change": 5, "reason": "restock"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 15)
        self.assertEqual(response.data["quantity_change"], 5)
        self.assertEqual(response.data["stock_quantity"], 15)

    def test_adjust_inventory_rejects_negative_result(self):
        response = self.client.post(
            f"/api/shop-admin/inventory/{self.product.id}/adjust/",
            {"quantity_change": -15, "reason": "sale"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)

    def test_invalid_adjustments_never_change_inventory(self):
        for payload in ({"quantity_change": 1.9}, {"quantity_change": 2147483647},
                        {"quantity_change": 1, "reason": "x" * 256}):
            with self.subTest(payload=payload):
                result = self.client.post(f"/api/shop-admin/inventory/{self.product.id}/adjust/", payload, format="json")
                self.assertEqual(result.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)

    def test_adjust_inventory_rejects_other_shop_product(self):
        response = self.client.post(
            f"/api/shop-admin/inventory/{self.other_product.id}/adjust/",
            {"quantity_change": 2, "reason": "restock"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_bulk_product_import_is_validated_and_all_or_nothing(self):
        csv_file = SimpleUploadedFile(
            "products.csv",
            b"name,category,price,stock_quantity,brand\n"
            b"Imported Beer,beer,250.00,4,Local\n",
            content_type="text/csv",
        )
        response = self.client.post(
            "/api/shop-admin/products/bulk-import/",
            {"file": csv_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["created_count"], 1)
        self.assertTrue(
            self.shop.products.filter(name="Imported Beer").exists()
        )

        invalid_file = SimpleUploadedFile(
            "invalid.csv",
            b"name,category,price,stock_quantity\n"
            b"Bad Product,unknown,250.00,4\n",
            content_type="text/csv",
        )
        response = self.client.post(
            "/api/shop-admin/products/bulk-import/",
            {"file": invalid_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("errors", response.data)
        self.assertEqual(
            self.shop.products.filter(name="Bad Product").count(),
            0,
        )

    def test_product_image_can_be_uploaded_and_removed(self):
        image_buffer = BytesIO()
        Image.new("RGB", (1, 1), color="red").save(image_buffer, format="PNG")
        response = self.client.post(
            f"/api/shop-admin/products/{self.product.id}/image/",
            {
                "image": SimpleUploadedFile(
                    "product.png",
                    image_buffer.getvalue(),
                    content_type="image/png",
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertTrue(self.product.image)

        response = self.client.delete(
            f"/api/shop-admin/products/{self.product.id}/image/"
        )
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertFalse(self.product.image)

    def test_application_license_detail_is_scoped_to_owning_shop_admin(self):
        document = ApplicationLicenseDocument.objects.create(
            shop=self.shop,
            license_number="LIC-INV-1",
            document=SimpleUploadedFile(
                "license.pdf",
                b"test document",
                content_type="application/pdf",
            ),
            expiry_date=date(2030, 1, 1),
        )
        response = self.client.get(
            f"/api/shop-admin/application/documents/{document.id}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], document.id)

        self.client.force_authenticate(user=self.other_admin)
        response = self.client.get(
            f"/api/shop-admin/application/documents/{document.id}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_shop_reports_return_orders_sales_and_top_products(self):
        customer = self.user_model.objects.create(
            username="report-customer",
            email="report-customer@example.com",
            user_type="customer",
        )
        order = Order.objects.create(
            customer=customer,
            shop=self.shop,
            status="completed",
            subtotal="998.00",
            total_amount="1048.00",
        )
        from orders.models import OrderItem

        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            unit_price="499.00",
            quantity=2,
            subtotal="998.00",
        )
        response = self.client.get("/api/shop-admin/reports/?range=7d")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["orders_count"], 1)
        self.assertEqual(response.data["sales"], "998.00")
        self.assertEqual(
            response.data["top_products"][0]["product_id"],
            self.product.id,
        )

    def test_shop_notification_preferences_and_mark_all_read(self):
        from customers.models import UserNotificationPreference
        from shop_admin.models import ShopAdminNotification

        response = self.client.patch(
            "/api/shop-admin/notifications/preferences/",
            {"promotions_enabled": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["promotions_enabled"])
        self.assertTrue(
            UserNotificationPreference.objects.filter(
                user=self.shop_admin,
            ).exists()
        )
        ShopAdminNotification.objects.create(
            shop_admin=self.shop_admin,
            title="Order",
            message="New order",
        )
        response = self.client.post(
            "/api/shop-admin/notifications/read-all/",
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated_count"], 1)
