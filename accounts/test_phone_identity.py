from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User


class CustomerRegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_registration_applies_django_password_validators(self):
        response = self.client.post(
            reverse("register"),
            {"username": "weak", "email": "weak@example.com", "password": "password"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    def test_registration_creates_a_customer_with_a_hashed_password(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "new-customer",
                "email": "new-customer@example.com",
                "phone": "+919876543210",
                "password": "StrongCustomerPassword#123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["username"], "new-customer")
        self.assertNotIn("password", response.data)
        user = User.objects.get(email="new-customer@example.com")
        self.assertEqual(user.user_type, "customer")
        self.assertTrue(user.check_password("StrongCustomerPassword#123"))
