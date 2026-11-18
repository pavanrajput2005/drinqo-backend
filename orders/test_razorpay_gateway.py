"""Payment transport/signature test source. Not executed by this change.

Every transport is mocked; these tests require no provider credentials or DB.
Run only when the project's test-execution restrictions are explicitly lifted.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from orders.razorpay_gateway import (
    API_ROOT, MAX_BODY_BYTES, TIMEOUT, GatewayError, RazorpayGateway,
    verify_checkout_signature, verify_webhook,
)


CONFIG = {
    "enabled": True,
    "key_id": "rzp_test_syntheticOnly",
    "key_secret": "synthetic-test-api-secret-12345678",
    "webhook_secrets": ["synthetic-test-webhook-secret-12345678"],
}


def order_entity(**changes):
    return {
        "entity": "order", "id": "order_synthetic1", "amount": 105000,
        "amount_paid": 0, "amount_due": 105000, "currency": "INR",
        "status": "created", "receipt": "drq_synthetic1",
        "transfers": [{"entity": "transfer", "id": "trf_synthetic1", "source": "order_synthetic1",
                       "recipient": "acc_synthetic1", "amount": 100000, "currency": "INR", "on_hold": False}],
        **changes,
    }


def payment_entity(**changes):
    return {
        "entity": "payment", "id": "pay_synthetic1", "amount": 105000,
        "currency": "INR", "status": "captured", "captured": True,
        "order_id": "order_synthetic1", "amount_refunded": 0,
        **changes,
    }


@override_settings(DRINQO_RAZORPAY=CONFIG)
class RazorpayGatewayTests(SimpleTestCase):
    def test_disabled_missing_and_live_configuration_fail_before_transport(self):
        for config in ({}, {**CONFIG, "enabled": False}, {**CONFIG, "enabled": "true"},
                       {**CONFIG, "key_id": "rzp_live_unapproved"},
                       {**CONFIG, "key_id": "rzp_test_" + "a" * 56},
                       {**CONFIG, "key_secret": "short"},
                       {**CONFIG, "webhook_secrets": []},
                       {**CONFIG, "webhook_secrets": [CONFIG["key_secret"]]}):
            with self.subTest(config_keys=tuple(config)), override_settings(DRINQO_RAZORPAY=config):
                with patch("orders.razorpay_gateway.requests.Session") as session:
                    with self.assertRaises(GatewayError) as caught:
                        RazorpayGateway()
                    self.assertEqual(caught.exception.status_code, 503)
                    session.assert_not_called()

    def test_checkout_hmac_binds_both_provider_identifiers(self):
        signature = hmac.new(CONFIG["key_secret"].encode(), b"order_synthetic1|pay_synthetic1", hashlib.sha256).hexdigest()
        self.assertTrue(verify_checkout_signature("order_synthetic1", "pay_synthetic1", signature))
        self.assertFalse(verify_checkout_signature("order_other", "pay_synthetic1", signature))
        self.assertFalse(verify_checkout_signature("order_synthetic1", "pay_other", signature))
        for invalid in (None, "", "x" * 64, signature + " ", 123):
            self.assertFalse(verify_checkout_signature("order_synthetic1", "pay_synthetic1", invalid))

    def test_webhook_authenticates_exact_bytes_and_retained_secrets(self):
        body = b'{"event":"payment.captured"}'
        old_secret = CONFIG["webhook_secrets"][0]
        signature = hmac.new(old_secret.encode(), body, hashlib.sha256).hexdigest()
        with override_settings(DRINQO_RAZORPAY={**CONFIG, "webhook_secrets": ["new-synthetic-webhook-secret-12345678", old_secret]}):
            self.assertTrue(verify_webhook(body, signature))
            self.assertFalse(verify_webhook(body + b" ", signature))
            self.assertFalse(verify_webhook(body.decode(), signature))
            self.assertFalse(verify_webhook(b"x" * (MAX_BODY_BYTES + 1), signature))
        with override_settings(DRINQO_RAZORPAY={**CONFIG, "webhook_secrets": ["new-synthetic-webhook-secret-12345678"]}):
            self.assertFalse(verify_webhook(body, signature))

    def test_order_creation_pins_server_amount_and_shop_split(self):
        gateway = RazorpayGateway()
        with patch.object(gateway, "_request", return_value=order_entity()) as request:
            result = gateway.create_order("drq_synthetic1", 105000, 100000, "acc_synthetic1")
        self.assertEqual(result["id"], "order_synthetic1")
        request.assert_called_once_with("POST", "/orders", payload={
            "amount": 105000, "currency": "INR", "receipt": "drq_synthetic1", "partial_payment": False,
            "transfers": [{"account": "acc_synthetic1", "amount": 100000, "currency": "INR", "on_hold": False}],
        })

    def test_expanded_route_transfer_fetch_matches_official_order_expansion(self):
        gateway = RazorpayGateway()
        entity = order_entity()
        transfer = entity["transfers"][0]
        expanded = {**entity, "transfers": {"entity": "collection", "count": 1, "items": [transfer]}}
        with patch.object(gateway, "_request", return_value=expanded) as request:
            transfers = gateway.fetch_order_transfers("order_synthetic1")
        request.assert_called_once_with("GET", "/orders/order_synthetic1", params={"expand[]": "transfers"})
        self.assertEqual(transfers, [transfer])
        self.assertTrue(gateway.validate_order_split({**entity, "transfers": transfers}, 100000, "acc_synthetic1"))
        for invalid in ({**expanded, "id": "order_other"},
                        {**expanded, "transfers": {"entity": "collection", "count": 2, "items": [transfer]}},
                        {key: value for key, value in entity.items() if key != "transfers"}):
            with patch.object(gateway, "_request", return_value=invalid):
                with self.assertRaises(GatewayError):
                    gateway.fetch_order_transfers("order_synthetic1")
        with self.assertRaises(GatewayError):
            gateway.validate_order_split(entity, 99999, "acc_synthetic1")
        with self.assertRaises(GatewayError):
            gateway.validate_order_split(entity, 100000, "acc_other")

    def test_identifiers_fit_database_columns_and_reject_trailing_path(self):
        gateway = RazorpayGateway()
        with patch.object(gateway, "_request") as request:
            for invalid in ("pay_" + "a" * 61, "pay_a/other", "pay_a?x=y", "pay_अ"):
                with self.assertRaises(GatewayError):
                    gateway.fetch_payment(invalid)
            request.assert_not_called()

    def test_untrusted_amounts_identifiers_and_missing_split_fail_closed(self):
        gateway = RazorpayGateway()
        for arguments in (("drq_synthetic1", True, 100000, "acc_synthetic1"),
                          ("drq_synthetic1", 105000, 105000, "acc_synthetic1"),
                          ("drq_synthetic1", 105000, 100000, "../other")):
            with self.subTest(arguments=arguments), patch.object(gateway, "_request") as request:
                with self.assertRaises(GatewayError):
                    gateway.create_order(*arguments)
                request.assert_not_called()
        for response in (order_entity(amount=105001), order_entity(transfers=[]),
                         order_entity(receipt="other"), order_entity(currency="USD"),
                         order_entity(partial_payment=True)):
            with self.subTest(response=response), patch.object(gateway, "_request", return_value=response):
                with self.assertRaises(GatewayError):
                    gateway.create_order("drq_synthetic1", 105000, 100000, "acc_synthetic1")

    def test_find_receipt_filters_exactly_and_rejects_duplicate_matches(self):
        gateway = RazorpayGateway()
        for entities, expected in (([], None), ([order_entity(receipt="prefix_drq_synthetic1")], None),
                                   ([order_entity()], "order_synthetic1")):
            response = {"entity": "collection", "count": len(entities), "items": entities}
            with patch.object(gateway, "_request", return_value=response):
                result = gateway.find_order("drq_synthetic1")
                self.assertEqual(result["id"] if result else None, expected)
        with patch.object(gateway, "_request", return_value={"entity": "collection", "count": 2, "items": [order_entity(), order_entity(id="order_other", transfers=[])]}):
            with self.assertRaises(GatewayError) as caught:
                gateway.find_order("drq_synthetic1")
            self.assertEqual(caught.exception.code, "gateway_reconciliation_required")

    def test_pagination_is_bounded_and_never_returns_partial_success(self):
        gateway = RazorpayGateway()
        first = [order_entity(id=f"order_synthetic{n}", transfers=[], receipt="other") for n in range(100)]
        with patch("orders.razorpay_gateway.MAX_PAGES", 1), patch.object(gateway, "_request", return_value={"entity": "collection", "count": 100, "items": first}):
            with self.assertRaises(GatewayError) as caught:
                gateway.find_order("drq_synthetic1")
            self.assertEqual(caught.exception.code, "gateway_reconciliation_required")

    def test_payment_fetch_validates_binding_and_strips_customer_data(self):
        gateway = RazorpayGateway()
        with patch.object(gateway, "_request", return_value=payment_entity(email="private@example.test", contact="9999999999", vpa="private@upi", notes={"private": "data"})):
            result = gateway.fetch_payment("pay_synthetic1")
        for private in ("email", "contact", "vpa", "notes"):
            self.assertNotIn(private, result)
        for invalid in (payment_entity(id="pay_other"), payment_entity(captured="true"),
                        payment_entity(amount=True), payment_entity(status=[])):
            with patch.object(gateway, "_request", return_value=invalid):
                with self.assertRaises(GatewayError):
                    gateway.fetch_payment("pay_synthetic1")
        with patch.object(gateway, "_request", return_value={"entity": "collection", "count": 1, "items": [payment_entity(order_id="order_other")]}):
            with self.assertRaises(GatewayError):
                gateway.fetch_order_payments("order_synthetic1")

    def test_all_order_payments_use_documented_endpoint_without_pagination(self):
        gateway = RazorpayGateway()
        payment = payment_entity(status="authorized", captured=False)
        with patch.object(gateway, "_request", return_value={"entity": "collection", "count": 1, "items": [payment]}) as request:
            result = gateway.fetch_order_payments("order_synthetic1")
        request.assert_called_once_with("GET", "/orders/order_synthetic1/payments")
        self.assertEqual(result[0]["status"], "authorized")
        self.assertFalse(result[0]["captured"])

    def test_refund_reverses_route_transfer_and_validates_receipt(self):
        gateway = RazorpayGateway()
        entity = {"entity": "refund", "id": "rfnd_synthetic1", "payment_id": "pay_synthetic1", "amount": 105000, "currency": "INR", "receipt": "refund_synthetic1", "status": "pending"}
        with patch.object(gateway, "_request", return_value=entity) as request:
            result = gateway.refund("pay_synthetic1", 105000, "refund_synthetic1")
        self.assertEqual(result["status"], "pending")
        request.assert_called_once_with("POST", "/payments/pay_synthetic1/refund", payload={"amount": 105000, "receipt": "refund_synthetic1", "speed": "normal", "reverse_all": True})
        with patch.object(gateway, "_request", return_value={**entity, "receipt": "other"}):
            with self.assertRaises(GatewayError):
                gateway.refund("pay_synthetic1", 105000, "refund_synthetic1")

    def _mock_transport(self, *, body=None, status=200, error=None):
        factory = patch("orders.razorpay_gateway.requests.Session")
        session_factory = factory.start()
        self.addCleanup(factory.stop)
        session = session_factory.return_value.__enter__.return_value
        response = MagicMock()
        response.status_code = status
        response.iter_content.return_value = [json.dumps(body or {}).encode()]
        session.request.return_value.__enter__.return_value = response
        if error is not None:
            session.request.side_effect = error
        return session, response

    def test_transport_has_fixed_origin_tls_timeout_and_no_proxy_or_redirects(self):
        session, _ = self._mock_transport(body=payment_entity())
        RazorpayGateway().fetch_payment("pay_synthetic1")
        self.assertFalse(session.trust_env)
        args = session.request.call_args
        self.assertEqual(args.args, ("GET", API_ROOT + "/payments/pay_synthetic1"))
        self.assertEqual(args.kwargs["timeout"], TIMEOUT)
        self.assertIs(args.kwargs["verify"], True)
        self.assertIs(args.kwargs["allow_redirects"], False)
        self.assertIs(args.kwargs["stream"], True)
        self.assertEqual(args.kwargs["auth"], (CONFIG["key_id"], CONFIG["key_secret"]))
        self.assertEqual(session.mount.call_args.args[1].max_retries.total, 0)
        session.request.assert_called_once()

    def test_network_errors_are_sanitized_and_never_retried(self):
        session, _ = self._mock_transport(error=requests.Timeout("private secret response text"))
        with self.assertRaises(GatewayError) as caught:
            RazorpayGateway().create_order("drq_synthetic1", 105000, 100000, "acc_synthetic1")
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn(CONFIG["key_secret"], str(caught.exception))
        session.request.assert_called_once()

    def test_http_error_redirect_invalid_json_and_oversized_response_are_rejected(self):
        session, response = self._mock_transport(status=302)
        gateway = RazorpayGateway()
        with self.assertRaises(GatewayError):
            gateway.fetch_payment("pay_synthetic1")
        response.status_code = 200
        response.iter_content.return_value = [b"not JSON"]
        with self.assertRaises(GatewayError):
            gateway.fetch_payment("pay_synthetic1")
        response.iter_content.return_value = [b"x" * (MAX_BODY_BYTES + 1)]
        with self.assertRaises(GatewayError):
            gateway.fetch_payment("pay_synthetic1")
        self.assertEqual(session.request.call_count, 3)
