"""Compatibility, privacy and retention guarantees for pre-retirement orders."""
import inspect
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .handover_privacy import protect_handover_request
from .models import HandoverAuditEvent, OrderHandoverOTP, StaffHandoverConfirmation
from .serializers import OrderSerializer
from .testing_handover import HandoverFixture


class HistoricalHandoverTests(HandoverFixture, TestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.row = OrderHandoverOTP.objects.create(
            order=self.order, challenge_id=uuid.uuid4(), ciphertext=b"historical",
            verification_digest="retained", key_version="old", issued_at=now,
            expires_at=now + timedelta(minutes=5), reissue_available_at=now,
            issue_window_started_at=now, issue_count=1,
        )
        self.event = HandoverAuditEvent.objects.create(
            order=self.order, actor=self.customer, challenge_id=self.row.challenge_id, event_type="issued",
        )

    def test_old_challenge_is_neither_disclosed_nor_consumed(self):
        original = OrderHandoverOTP.objects.values().get(pk=self.row.pk)
        for user in (None, self.customer, self.owner, self.admin):
            self.client.force_authenticate(user)
            for route in ("order-handover-otp", "order-handover-otp-reissue", "verify-pickup", "shop_admin_complete_order"):
                url = reverse(route, args=[self.order.pk])
                for response in (self.client.get(url), self.client.post(
                    url, {"challenge_id": str(self.row.challenge_id), "otp": "012345"}, format="json",
                )):
                    self.assertEqual(response.status_code, 410)
                    self.assertEqual(set(response.data), {"code", "detail"})
                    self.assertEqual(response.data["code"], "handover_otp_retired")
                    self.assertEqual(response["Cache-Control"], "no-store, private")
                    self.assertIn("Authorization", response["Vary"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ready")
        self.assertFalse(self.order.pickup_verified)
        self.assertFalse(StaffHandoverConfirmation.objects.exists())
        self.assertEqual(original, OrderHandoverOTP.objects.values().get(pk=self.row.pk))
        self.assertEqual(list(HandoverAuditEvent.objects.values_list("pk", flat=True)), [self.event.pk])
        data = OrderSerializer(self.order).data
        self.assertIsNone(data["handover_challenge_id"])
        for key in ("otp", "ciphertext", "verification_digest"):
            self.assertNotIn(key, data)

    def test_terminal_order_scrubs_historical_secrets_without_deleting_evidence(self):
        self.order.status = "cancelled"
        self.order.save(update_fields=["status"])
        self.row.refresh_from_db()
        self.assertIsNone(self.row.ciphertext)
        self.assertEqual(self.row.verification_digest, "")
        events = HandoverAuditEvent.objects.filter(pk=self.event.pk)
        with self.assertRaises(ValidationError):
            events.update(event_type="verified")
        with self.assertRaises(ValidationError):
            events.delete()
        self.event.refresh_from_db()
        self.assertEqual(self.event.challenge_id, self.row.challenge_id)
        self.assertEqual(self.event.event_type, "issued")

    def test_route_error_report_filter_masks_locals_and_json_body(self):
        request = RequestFactory().post("/handover", {"otp": "042731"}, content_type="application/json")
        protect_handover_request(request)
        with override_settings(DEBUG=True):
            reporter = request.exception_reporter_filter
            self.assertTrue(reporter.is_active(request))
            self.assertNotIn("042731", repr(reporter.get_post_parameters(request)))
            self.assertTrue(all(value == reporter.cleansed_substitute for _, value in
                                reporter.get_traceback_frame_variables(request, inspect.currentframe())))
        self.assertTrue(request.sensitive_body)
        self.assertTrue(request.sensitive_response)
