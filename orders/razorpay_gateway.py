"""Razorpay Test Mode transport and signature verification.

No keys are loaded and no requests are made on import. Live keys are deliberately
unsupported pending merchant/category and Route approval. Callers must reconcile
ambiguous writes; this module never retries a payment-provider request.

Provider contracts checked against Razorpay's Orders, Route, Refunds and Webhook
documentation. A checkout signature authenticates identifiers, not capture.
"""
import hashlib
import hmac
import json
import re

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables


API_ROOT = "https://api.razorpay.com/v1"
TIMEOUT = (3.05, 10)
MAX_BODY_BYTES = 1_048_576
PAGE_SIZE = 100
MAX_PAGES = 10
_SIGNATURE = re.compile(r"[0-9a-f]{64}")
_IDS = {
    "order": re.compile(r"order_[A-Za-z0-9]{1,58}"),
    "payment": re.compile(r"pay_[A-Za-z0-9]{1,60}"),
    "account": re.compile(r"acc_[A-Za-z0-9]{1,60}"),
    "refund": re.compile(r"rfnd_[A-Za-z0-9]{1,59}"),
    "transfer": re.compile(r"trf_[A-Za-z0-9]{1,60}"),
}


class GatewayError(Exception):
    """Only safe, fixed messages cross the HTTP/service boundary."""

    def __init__(self, code, detail, status_code=503):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def _unavailable():
    return GatewayError("payment_unavailable", "Payment service is unavailable.")


def _invalid_response():
    return GatewayError("gateway_invalid_response", "Payment provider response could not be verified.", 502)


def _id(value, kind):
    if not isinstance(value, str) or _IDS[kind].fullmatch(value) is None:
        raise GatewayError("invalid_payment_identifier", "Invalid payment identifier.", 400)
    return value


def _amount(value, minimum=1):
    if type(value) is not int or not minimum <= value <= 999_999_999_999:
        raise GatewayError("invalid_payment_amount", "Invalid payment amount.", 400)
    return value


def _receipt(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40 or any(not 33 <= ord(c) <= 126 for c in value):
        raise GatewayError("invalid_payment_receipt", "Invalid payment reference.", 400)
    return value


@sensitive_variables()
def _configuration():
    config = getattr(settings, "DRINQO_RAZORPAY", None)
    if not isinstance(config, dict) or config.get("enabled") is not True:
        raise _unavailable()
    key_id = config.get("key_id")
    key_secret = config.get("key_secret")
    webhook_secrets = config.get("webhook_secrets")
    if (not isinstance(key_id, str)
            or re.fullmatch(r"rzp_test_[A-Za-z0-9]{1,55}", key_id) is None
            or not isinstance(key_secret, str) or not 16 <= len(key_secret) <= 1024
            or any(not 33 <= ord(c) <= 126 for c in key_secret)
            or not isinstance(webhook_secrets, list) or not 1 <= len(webhook_secrets) <= 3):
        raise _unavailable()
    for secret in webhook_secrets:
        if (not isinstance(secret, str) or not 32 <= len(secret) <= 1024
                or any(not 33 <= ord(c) <= 126 for c in secret)
                or hmac.compare_digest(secret, key_secret)):
            raise _unavailable()
    return key_id, key_secret, tuple(webhook_secrets)


@sensitive_variables()
def verify_checkout_signature(provider_order_id, payment_id, signature):
    """Use the server-stored provider order ID, never a client-selected binding."""
    _id(provider_order_id, "order")
    _id(payment_id, "payment")
    _, key_secret, _ = _configuration()
    if not isinstance(signature, str) or _SIGNATURE.fullmatch(signature) is None:
        return False
    payload = f"{provider_order_id}|{payment_id}".encode("ascii")
    expected = hmac.new(key_secret.encode("ascii"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@sensitive_variables()
def verify_webhook(raw_body, signature):
    """Authenticate exact body bytes; retained secrets support webhook retries."""
    _, _, webhook_secrets = _configuration()
    if (not isinstance(raw_body, bytes) or len(raw_body) > MAX_BODY_BYTES
            or not isinstance(signature, str) or _SIGNATURE.fullmatch(signature) is None):
        return False
    valid = False
    for secret in webhook_secrets:
        expected = hmac.new(secret.encode("ascii"), raw_body, hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(expected, signature) or valid
    return valid


@sensitive_variables()
def _entity(data, kind, statuses, binding=None):
    """Validate before returning only non-PII fields to application services."""
    try:
        if not isinstance(data, dict) or data.get("entity") != kind or data.get("currency") != "INR":
            raise ValueError
        _id(data.get("id"), kind)
        _amount(data.get("amount"))
        if data.get("status") not in statuses:
            raise ValueError
        if binding:
            _id(data.get(binding), "order" if binding == "order_id" else "payment")
    except (ValueError, TypeError, GatewayError):
        raise _invalid_response() from None
    fields = {"id", "entity", "amount", "currency", "status"}
    if kind == "order":
        fields.update({"receipt", "amount_paid", "amount_due", "partial_payment", "transfers"})
    elif kind == "payment":
        fields.update({"order_id", "captured", "amount_refunded", "refund_status"})
    elif kind == "refund":
        fields.update({"payment_id", "receipt"})
    return {field: data[field] for field in fields if field in data}


@sensitive_variables()
def _order(data):
    result = _entity(data, "order", {"created", "attempted", "paid"})
    try:
        for field in ("amount_due", "amount_paid"):
            if field in result:
                _amount(result[field], 0)
                if result[field] > result["amount"]:
                    raise ValueError
        if "partial_payment" in result and type(result["partial_payment"]) is not bool:
            raise ValueError
        if result.get("receipt") is not None:
            _receipt(result["receipt"])
        if "transfers" in result:
            raw_transfers = result["transfers"]
            if isinstance(raw_transfers, dict):
                expanded_items = raw_transfers.get("items")
                if (raw_transfers.get("entity") != "collection" or not isinstance(expanded_items, list)
                        or type(raw_transfers.get("count")) is not int or raw_transfers["count"] != len(expanded_items)):
                    raise ValueError
                raw_transfers = expanded_items
            if not isinstance(raw_transfers, list) or len(raw_transfers) > 10:
                raise ValueError
            transfers = []
            fields = {"id", "entity", "source", "recipient", "amount", "currency", "status", "on_hold", "amount_reversed"}
            for transfer in raw_transfers:
                if not isinstance(transfer, dict) or transfer.get("entity") != "transfer" or transfer.get("currency") != "INR":
                    raise ValueError
                _id(transfer.get("id"), "transfer")
                _id(transfer.get("recipient"), "account")
                _amount(transfer.get("amount"))
                if transfer.get("source") != result["id"]:
                    raise ValueError
                transfers.append({key: transfer[key] for key in fields if key in transfer})
            result["transfers"] = transfers
    except (ValueError, GatewayError):
        raise _invalid_response() from None
    return result


@sensitive_variables()
def parse_payment(data):
    result = _entity(data, "payment", {"created", "authorized", "captured", "refunded", "failed"}, "order_id")
    try:
        if type(result.get("captured")) is not bool:
            raise ValueError
        _amount(result.get("amount_refunded"), 0)
        if result["amount_refunded"] > result["amount"]:
            raise ValueError
    except (ValueError, GatewayError):
        raise _invalid_response() from None
    return result


@sensitive_variables()
def _refund(data):
    result = _entity(data, "refund", {"pending", "processed", "failed"}, "payment_id")
    if result.get("receipt") is not None:
        try:
            _receipt(result["receipt"])
        except GatewayError:
            raise _invalid_response() from None
    return result


class RazorpayGateway:
    @sensitive_variables()
    def __init__(self):
        self.key_id, self._key_secret, _ = _configuration()

    @sensitive_variables()
    def _request(self, method, path, *, payload=None, params=None):
        # Path construction is internal and every interpolated identifier is
        # validated. No caller-supplied URL, proxy, redirect or retry is allowed.
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
                with session.request(
                    method, API_ROOT + path,
                    auth=(self.key_id, self._key_secret), json=payload, params=params,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    timeout=TIMEOUT, allow_redirects=False, verify=True, stream=True,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise GatewayError("gateway_request_failed", "Payment provider request failed.")
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            raise _invalid_response()
                        chunks.append(chunk)
                    data = json.loads(b"".join(chunks))
                    if not isinstance(data, dict):
                        raise _invalid_response()
                    return data
        except (requests.RequestException, UnicodeError, ValueError, TypeError, RecursionError):
            # Never copy requests' exception text: it can contain credentials,
            # URLs, proxy details or provider-supplied customer information.
            raise GatewayError("gateway_request_failed", "Payment provider request failed.") from None

    @sensitive_variables()
    def create_order(self, receipt, amount, shop_amount, account_id):
        _receipt(receipt)
        _amount(amount)
        _amount(shop_amount)
        _id(account_id, "account")
        if shop_amount >= amount:
            raise GatewayError("invalid_payment_split", "Invalid payment split.", 400)
        data = self._request("POST", "/orders", payload={
            "amount": amount, "currency": "INR", "receipt": receipt,
            "partial_payment": False,
            "transfers": [{"account": account_id, "amount": shop_amount, "currency": "INR", "on_hold": False}],
        })
        result = _order(data)
        if (result["amount"] != amount or result.get("receipt") != receipt
                or result["status"] != "created" or result.get("partial_payment") is True):
            raise _invalid_response()
        self.validate_order_split(result, shop_amount, account_id)
        return result

    @staticmethod
    @sensitive_variables()
    def validate_order_split(entity, shop_amount, account_id):
        """Verify a recovered order's routing against its immutable local split."""
        _amount(shop_amount)
        _id(account_id, "account")
        result = _order(entity)
        transfers = result.get("transfers", [])
        if (len(transfers) != 1 or transfers[0]["recipient"] != account_id
                or transfers[0]["amount"] != shop_amount or transfers[0].get("on_hold") not in (False, 0)
                or result.get("partial_payment") is True or shop_amount >= result["amount"]):
            raise _invalid_response()
        return True

    @sensitive_variables()
    def _collection(self, path, validator, params=None):
        """Fail closed on a truncated listing, duplicate ID or bad pagination."""
        items = []
        seen = set()
        for page in range(MAX_PAGES):
            query = {**(params or {}), "count": PAGE_SIZE, "skip": page * PAGE_SIZE}
            data = self._request("GET", path, params=query)
            batch = data.get("items")
            if (data.get("entity") != "collection" or not isinstance(batch, list)
                    or type(data.get("count")) is not int or data["count"] != len(batch)
                    or len(batch) > PAGE_SIZE):
                raise _invalid_response()
            for raw in batch:
                item = validator(raw)
                if item["id"] in seen:
                    raise GatewayError("gateway_reconciliation_required", "Payment reconciliation is required.", 409)
                seen.add(item["id"])
                items.append(item)
            if len(batch) < PAGE_SIZE:
                return items
        raise GatewayError("gateway_reconciliation_required", "Payment reconciliation is required.", 409)

    @sensitive_variables()
    def find_order(self, receipt):
        """None does not prove an earlier ambiguous POST failed; never retry it."""
        _receipt(receipt)
        matches = [item for item in self._collection("/orders", _order, {"receipt": receipt}) if item.get("receipt") == receipt]
        if len(matches) > 1:
            raise GatewayError("gateway_reconciliation_required", "Payment reconciliation is required.", 409)
        return matches[0] if matches else None

    @sensitive_variables()
    def fetch_order(self, order_id):
        _id(order_id, "order")
        result = _order(self._request("GET", f"/orders/{order_id}"))
        if result["id"] != order_id:
            raise _invalid_response()
        return result

    @sensitive_variables()
    def fetch_order_transfers(self, order_id):
        # Razorpay's Route retrieval expands transfers on the Orders endpoint.
        # Do not filter by status: reconciliation must observe every transfer.
        _id(order_id, "order")
        result = _order(self._request("GET", f"/orders/{order_id}", params={"expand[]": "transfers"}))
        if result["id"] != order_id or "transfers" not in result:
            raise _invalid_response()
        return result["transfers"]

    @sensitive_variables()
    def fetch_payment(self, payment_id):
        _id(payment_id, "payment")
        result = parse_payment(self._request("GET", f"/payments/{payment_id}"))
        if result["id"] != payment_id:
            raise _invalid_response()
        return result

    @sensitive_variables()
    def fetch_order_payments(self, order_id):
        _id(order_id, "order")
        # This endpoint is documented as returning all order payments and has no
        # documented count/skip query. Do not invent pagination parameters.
        data = self._request("GET", f"/orders/{order_id}/payments")
        batch = data.get("items")
        if (data.get("entity") != "collection" or not isinstance(batch, list)
                or type(data.get("count")) is not int or data["count"] != len(batch)
                or len(batch) > PAGE_SIZE * MAX_PAGES):
            raise _invalid_response()
        payments = [parse_payment(item) for item in batch]
        if len({payment["id"] for payment in payments}) != len(payments):
            raise _invalid_response()
        if any(payment["order_id"] != order_id for payment in payments):
            raise _invalid_response()
        return payments

    @sensitive_variables()
    def refund(self, payment_id, amount, receipt):
        _id(payment_id, "payment")
        _amount(amount)
        _receipt(receipt)
        result = _refund(self._request("POST", f"/payments/{payment_id}/refund", payload={
            "amount": amount, "receipt": receipt, "speed": "normal", "reverse_all": True,
        }))
        if result["payment_id"] != payment_id or result["amount"] != amount or result.get("receipt") != receipt:
            raise _invalid_response()
        return result

    @sensitive_variables()
    def fetch_refunds(self, payment_id):
        _id(payment_id, "payment")
        refunds = self._collection(f"/payments/{payment_id}/refunds", _refund)
        if any(refund["payment_id"] != payment_id for refund in refunds):
            raise _invalid_response()
        return refunds
