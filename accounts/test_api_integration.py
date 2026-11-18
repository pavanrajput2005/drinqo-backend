from datetime import date

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop, ShopLicenseDocument


class JWTApiIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "StrongTestPassword#123"

    def create_user(self, username, email, user_type):
        return User.objects.create_user(
            username=username,
            email=email,
            password=self.password,
            user_type=user_type,
        )

    def get_access_token(self, user):
        response = self.client.post(
            reverse("login"),
            {"email": user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return response.data["access"]

    def test_customer_shop_admin_and_super_admin_accounts_use_jwt(self):
        for user_type in ("customer", "shop_admin", "super_admin"):
            with self.subTest(user_type=user_type):
                user = self.create_user(
                    username=f"{user_type}-user",
                    email=f"{user_type}@example.com",
                    user_type=user_type,
                )
                access = self.get_access_token(user)
                response = self.client.get(
                    reverse("me"),
                    HTTP_AUTHORIZATION=f"Bearer {access}",
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["id"], user.id)
                self.assertEqual(response.data["user_type"], user_type)

    def test_super_admin_api_preserves_role_based_access(self):
        admin = self.create_user(
            "jwt-super-admin",
            "jwt-super-admin@example.com",
            "super_admin",
        )
        customer = self.create_user(
            "jwt-customer",
            "jwt-customer@example.com",
            "customer",
        )

        admin_response = self.client.get(
            reverse("super-admin-me"),
            HTTP_AUTHORIZATION=f"Bearer {self.get_access_token(admin)}",
        )
        customer_response = self.client.get(
            reverse("super-admin-me"),
            HTTP_AUTHORIZATION=f"Bearer {self.get_access_token(customer)}",
        )

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.data["id"], admin.id)
        self.assertEqual(customer_response.status_code, 403)

    def test_shop_admin_api_accepts_jwt_and_preserves_role(self):
        shop_admin = self.create_user(
            "jwt-shop-admin",
            "jwt-shop-admin@example.com",
            "shop_admin",
        )
        shop = Shop.objects.create(
            owner=shop_admin,
            shop_name="JWT Shop",
            phone="9876543210",
            address_line1="1 Test Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            license_number="JWT-SHOP-LICENSE",
            license_status="verified",
            is_active=True,
        )
        ShopLicenseDocument.objects.create(
            shop=shop,
            license_number=shop.license_number,
            document="private_shop_licenses/jwt-shop-license.pdf",
            expiry_date=date(2030, 1, 1),
            verification_status="verified",
        )

        response = self.client.get(
            reverse("shop_admin_me"),
            HTTP_AUTHORIZATION=f"Bearer {self.get_access_token(shop_admin)}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user_type"], "shop_admin")
