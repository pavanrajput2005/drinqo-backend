from datetime import date

from django.urls import reverse
from django.utils import timezone
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import User
from customers.age_verification import has_approved_age_proof


class AgeSelfDeclarationApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="self-declaration-customer",
            email="self-declaration@example.com",
            password="StrongPassword#123",
            user_type="customer",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def post_age(self, payload):
        return self.client.post(reverse("age_verification"), payload, format="json")

    def test_missing_dob_cannot_be_acknowledged(self):
        response = self.post_age({"age_self_declaration": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("date_of_birth", response.data)

    def test_authoritative_proof_check_does_not_select_new_consent_columns(self):
        self.user.date_of_birth = date(2000, 1, 1)
        self.user.save(update_fields=["date_of_birth"])
        with CaptureQueriesContext(connection) as queries:
            self.assertFalse(has_approved_age_proof(self.user))
        session_queries = [
            query["sql"] for query in queries.captured_queries
            if "customers_customerverificationsession" in query["sql"]
        ]
        self.assertTrue(session_queries)
        self.assertTrue(
            all("consent_at" not in query.lower() for query in session_queries)
        )

    def test_underage_customer_cannot_declare(self):
        underage = timezone.localdate().replace(year=timezone.localdate().year - 20)
        response = self.post_age(
            {"date_of_birth": underage.isoformat(), "age_self_declaration": True}
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.age_self_declared)

    def test_eligible_declaration_survives_login_and_does_not_verify_checkout_age(self):
        response = self.post_age({"date_of_birth": "2000-01-01"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["age_self_declared"])
        response = self.post_age({"age_self_declaration": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["age_self_declared"])
        self.assertIsNotNone(response.data["age_self_declared_at"])
        self.assertFalse(response.data["age_verified"])

        self.client.force_authenticate(user=None)
        login = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "StrongPassword#123"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + login.data["access"])
        me = self.client.get(reverse("me"))
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.data["age_self_declared"])
        self.assertFalse(me.data["age_verified"])

        checkout = self.client.post(reverse("checkout-preview"), {}, format="json")
        # Declaration remains distinct from document verification. The current
        # checkout policy first requires the complete address/profile, absent here.
        self.assertEqual(checkout.status_code, 400)
        self.assertEqual(checkout.data["code"], "profile_incomplete")
