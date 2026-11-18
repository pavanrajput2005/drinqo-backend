from datetime import date
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import CustomerVerificationSession
from shop_admin.models import SuperAdminAuditLog


class AgeProofHardeningTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.customer = User.objects.create_user(username="age-proof-customer", email="age-proof@example.com", password="password", user_type="customer")
        self.reviewer = User.objects.create_user(username="age-proof-reviewer", email="age-proof-reviewer@example.com", password="password", user_type="super_admin")
        self.client = APIClient()
        self.client.force_authenticate(self.customer)

    def payload(self, **overrides):
        return {
            "document_type": "age_proof",
            "consent": True,
            "consent_policy_version": "age-proof-v1",
            "document": SimpleUploadedFile("age-proof.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
            **overrides,
        }

    def test_age_proof_requires_server_validated_consent(self):
        for payload in (self.payload(consent=False), self.payload(consent_policy_version="old-policy")):
            response = self.client.post(reverse("customer-verification-sessions"), payload, format="multipart")
            self.assertEqual(response.status_code, 400)

    def test_pending_conflict_and_rejection_allows_new_session(self):
        first = self.client.post(reverse("customer-verification-sessions"), self.payload(), format="multipart")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.data["consent_policy_version"], "age-proof-v1")
        self.assertIsNotNone(first.data["consent_at"])
        conflict = self.client.post(reverse("customer-verification-sessions"), self.payload(), format="multipart")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data, {"detail": "An age-proof verification is already pending."})
        CustomerVerificationSession.objects.filter(pk=first.data["id"]).update(status="rejected")
        retry = self.client.post(reverse("customer-verification-sessions"), self.payload(), format="multipart")
        self.assertEqual(retry.status_code, 201)
        self.assertNotEqual(retry.data["id"], first.data["id"])

    def test_verified_customer_cannot_upload_new_age_proof(self):
        verified = CustomerVerificationSession.objects.create(
            user=self.customer, document="private_customer_verification/old.pdf", document_type="age_proof",
            status="approved", reviewed_by=self.reviewer, reviewed_at=timezone.now(),
            consent_at=timezone.now(), consent_policy_version="age-proof-v1",
        )
        self.customer.date_of_birth = date(2000, 1, 1)
        self.customer.age_verified = True
        self.customer.age_verified_at = timezone.now()
        self.customer.save(update_fields=["date_of_birth", "age_verified", "age_verified_at"])
        SuperAdminAuditLog.objects.create(
            actor=self.reviewer, actor_email=self.reviewer.email, action="customer_document_approved",
            resource_type="verification_session", resource_id=verified.id, summary="Approved age proof.",
            metadata={"age_proof": True, "confirmed_date_of_birth": "2000-01-01", "age_verified": True},
        )
        response = self.client.post(reverse("customer-verification-sessions"), self.payload(), format="multipart")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, {"detail": "Age verification is already complete."})

    def test_document_type_is_restricted_and_customer_cannot_download_admin_file(self):
        rejected = self.client.post(reverse("customer-verification-sessions"), self.payload(document_type="unsupported"), format="multipart")
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("document_type", rejected.data)
        self.assertEqual(self.client.get("/api/super-admin/verification-sessions/999/document/").status_code, 403)

    def test_database_constraint_rejects_duplicate_pending_age_proofs(self):
        CustomerVerificationSession.objects.create(user=self.customer, document="private_customer_verification/one.pdf", document_type="age_proof")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomerVerificationSession.objects.create(user=self.customer, document="private_customer_verification/two.pdf", document_type="age_proof")
