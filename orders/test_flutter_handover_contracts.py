"""Verify actual routed Shop APIs supply the data the handover payload needs."""
from django.test import TestCase
from .test_staff_handover import StaffHandoverFixture


class FlutterStaffHandoverContractTests(StaffHandoverFixture, TestCase):
    def test_active_shop_list_and_detail_expose_customer_id(self):
        response = self.client.get("/api/shop-admin/orders/")
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data if item["id"] == self.order.pk)
        self.assertEqual(row["customer_id"], self.customer.pk)
        detail = self.client.get(f"/api/shop-admin/orders/{self.order.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["customer_id"], row["customer_id"])
        response = self.confirm(customer_id=detail.data["customer_id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"order_id", "status", "pickup_mode", "pickup_verified", "pickup_verified_at", "pickup_verified_by", "handover_method", "confirmation_id", "already_completed"})

    def test_wrong_customer_error_is_a_string_field(self):
        response = self.confirm(customer_id=self.other.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"customer_id": "Customer does not match this order."})

    def test_ineligible_error_shapes(self):
        self.order.status = "preparing"
        self.order.save()
        response = self.confirm()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"code": "order_not_ready", "detail": "Order must be ready and awaiting handover."})
        self.order.status, self.order.payment_status = "ready", "pending"
        self.order.save()
        response = self.confirm()
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json(), {"code": "payment_required", "detail": "Verified captured payment is required."})
