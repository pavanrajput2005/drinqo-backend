# DRINQO Razorpay payment integration

This is a backend implementation for **Razorpay Test Mode**. It does not
activate a merchant account, enable real charges, or provision linked accounts.
The backend migrations and isolated PostgreSQL regression suite were executed;
no live provider request or Flutter change was made. See
`docs/sahil_flutter_payment_handoff.md` for the client integration contract.

The API contracts below describe `orders/payments.py`, `orders/payment_views.py` and `orders/urls.py`. All example identifiers are illustrative. No runtime behavior has been verified.

## Live activation is blocked

DRINQO has no approved Razorpay merchant account. Razorpay's public merchant terms list alcohol among prohibited products and services. A licensed shop alone does not establish permission to process DRINQO orders through Razorpay. Obtain a written eligibility decision for the actual licensed-shop, vehicle-handover marketplace before pursuing activation; if Razorpay declines, select an eligible provider instead. Do not misdescribe alcohol transactions as delivery fees or platform services. [Razorpay merchant terms](https://razorpay.com/terms/)

Shop settlement requires Razorpay Route, approved linked accounts and the provider's marketplace onboarding requirements. Route has separate eligibility requirements, including financial and payer/payee transparency criteria. Neither a normal gateway account nor a test account establishes live Route eligibility. [Route prerequisites and eligibility](https://razorpay.com/docs/payments/route/)

This source rejects live keys. Changing keys alone must not be treated as the go-live procedure.

## Confirmed business rules

1. The customer places an order. Checkout preserves the minimum ₹500 product subtotal, age verification, licensing, geofence, vehicle ownership and inventory checks.
2. Orders of ₹15,000 or more require the existing Super Admin approval. Payment cannot supply that approval.
3. The Shop Admin accepts the order, producing canonical status `confirmed` and the existing acceptance notification.
4. The customer pays the server-calculated total after confirmation.
5. Backend verification of captured payment permits `confirmed → preparing → ready`.
6. Physical vehicle handover requires both verified payment and the existing Shop Admin OTP verification. Payment does not replace the OTP.

The only additional customer charge is **₹50 Platform fee**. There is no added delivery charge. DRINQO remains a vehicle-handover platform.

For example, a product subtotal of ₹500 produces a total of ₹550. The intended gross Route allocation is ₹500 to the licensed shop's linked account and ₹50 retained for DRINQO. Provider fees, taxes and adjustments affect net proceeds; ₹50 is not a promise of net revenue. Transfer and bank settlement are distinct from successful customer payment. [Route transfer flow](https://razorpay.com/docs/payments/route/transfer-funds-to-linked-accounts/)

**Fee funding is an unresolved live blocker.** Gateway/Route fees and taxes can exceed the fixed ₹50 remainder, especially for high-value orders. Obtain the provider's approved fee-bearer/funding arrangement and verify successful transfers and bank settlement before promising the full product subtotal to a shop. The code does not add another customer charge or silently reduce the shop's allocation to fund gateway fees. Transfer/settlement monitoring is still required before live use.

## Backward compatibility

- Existing `application_fee` database and JSON fields remain. The new read-only `platform_fee` field aliases the same amount. **Never add the two fields together.**
- Customer checkout, order detail/history, payment status and administrator order responses use the same underlying fee.
- Existing `payment_method` and `payment_status` fields remain available.
- The customer payment-status URL remains unchanged.
- Historical orders are not rewritten. Existing `accepted` orders retain their compatibility path, subject to payment verification where preparation or handover is attempted.
- A historical `payment_status="paid"` value alone is not verified gateway evidence. It must not permit handover without the new payment ledger's captured-payment evidence. Historical completed orders remain historical records.
- Existing old Flutter builds cannot complete the new payment flow without integration work. No fake-paid or manual Shop Admin payment shortcut is introduced.

## Configuration and account prerequisites

The source builds its `DRINQO_RAZORPAY` Django settings dictionary lazily from
environment variables. No credentials are committed. The integration remains
disabled until `RAZORPAY_PAYMENT_GATEWAY_MODE=test`,
`RAZORPAY_TEST_MODE_ENABLED=true`, and valid Test Mode values are
supplied in an approved test environment. See
`docs/razorpay-test-mode.env.example`; do not copy that file with placeholder
values into a production configuration.

| Setting | Meaning |
| --- | --- |
| `enabled` | Must be boolean `True`; absent or false means disabled. |
| `key_id` | Test-mode Razorpay public key ID; only `rzp_test_` identifiers are accepted. |
| `key_secret` | Server-only Razorpay test API secret, 16–1,024 printable ASCII characters without whitespace. |
| `webhook_secrets` | List of 1–3 independent webhook secrets, each 32–1,024 printable ASCII characters without whitespace and different from `key_secret`; retain an older secret while the provider retries older signed events. |

Do not put API secrets, webhook secrets, handover OTP keys or customer JWTs in Flutter. The test key ID is public checkout configuration; the secret is not.

Each shop needs a separately reviewed `ShopPaymentAccount` mapping to the correct Razorpay linked-account ID. Mappings start disabled and are configured through a separately approved operator process. There is no account-mapping/onboarding API in this source change. Clients cannot choose or change the settlement account. Do not copy bank details into order or webhook records. Existing payment snapshots also bind the API key ID: changing that ID requires operator reconciliation rather than retrying with a different account.

Configure automatic payment capture in the approved Razorpay test account. An `authorized` payment is not a captured payment and does not unlock preparation or handover.

Dependencies are the project's existing Django, DRF, JWT authentication, PostgreSQL driver, `requests`, and the existing OTP cryptography dependency. This adapter uses the HTTPS API directly and does not require installing the Razorpay Python SDK. A reproducible dependency lock and startup/import validation in the approved isolated environment remain necessary; no packages were installed here.

## Customer API contracts

All customer payment APIs require the existing Django customer access JWT and enforce order ownership. Shop Admin and Super Admin roles cannot impersonate the customer to initiate or verify payment. Amount, currency, fee and settlement destination come from backend records.

### Initiate payment

`POST /api/orders/{order_id}/payment/initiate/`

```json
{}
```

Call only after Shop confirmation and any required Super Admin approval. The backend rechecks age verification and restricted-shop continuation. It creates or returns the single payment record for the order and supplies the provider order ID and checkout options. An existing unresolved provider request must be reconciled; repeatedly pressing Pay must not create fresh gateway orders.

New initialization returns `201`; an existing/recovered initialization returns `200`. The response is:

```json
{
  "order_id": 123,
  "payment_status": "pending",
  "payment_state": "created",
  "gateway": "razorpay",
  "mode": "test",
  "amount": 55000,
  "currency": "INR",
  "platform_fee": "50",
  "checkout": {
    "key": "rzp_test_ExampleOnly123",
    "order_id": "order_ExampleOrder123",
    "amount": 55000,
    "currency": "INR",
    "name": "DRINQO"
  }
}
```

`amount` is integer paise for Razorpay checkout. It is not the decimal-rupee field returned by the existing order APIs. `platform_fee` in this response is a string in rupees and is already included in `amount`. The Flutter application must use the exact server-supplied checkout options.

If cancellation wins while initialization is in flight, the endpoint returns `409` with the same public fields and no `checkout` object. A captured existing payment also has no checkout object. Do not assume every successful response requests another checkout.

### Verify the checkout result

`POST /api/orders/{order_id}/payment/verify/`

```json
{
  "razorpay_order_id": "order_ExampleOrder123",
  "razorpay_payment_id": "pay_ExamplePayment123",
  "razorpay_signature": "<signature returned by Razorpay checkout>"
}
```

The backend checks the submitted order ID against its stored provider order ID, validates the checkout signature, and checks provider evidence for payment identity, order binding, full amount, currency and capture. A Flutter success callback alone must never mark an order paid. Authorized, failed, mismatched or refunded payments do not unlock the order.

The application must use the backend response and payment-status API to determine the result. If capture is still pending, retain a pending state and reconcile later.

Verification returns `200` after a matching capture, with no `checkout` object:

```json
{
  "order_id": 123,
  "payment_status": "paid",
  "payment_state": "captured",
  "gateway": "razorpay",
  "mode": "test",
  "amount": 55000,
  "currency": "INR",
  "platform_fee": "50"
}
```

### Reconcile interrupted payment

`POST /api/orders/{order_id}/payment/reconcile/`

```json
{}
```

Use this after a network interruption, app restart or pending callback. Reconciliation checks an existing backend payment record and provider evidence. It does not blindly create another payment or refund when the outcome of an earlier request is unknown. `200` returns the public fields shown above with the current `payment_state`; it does not necessarily mean `paid`. A missing mapping or unknown initialization outcome is an error, not permission to initiate a second provider order.

Possible ledger states are `creating`, `created`, `captured`, `refund_required`, `refunding`, `refunded` and `review`. Only `captured` with the full matching ledger evidence unlocks preparation/handover. A reconciliation response can precede a just-triggered refund attempt; refresh status rather than inventing a completed refund state.

### Read payment status

`GET /api/orders/{order_id}/payment-status/`

The existing amount fields remain; `platform_fee` aliases `application_fee`:

```json
{
  "order_id": 123,
  "payment_method": "online",
  "payment_status": "pending",
  "subtotal": "500.00",
  "application_fee": "50.00",
  "platform_fee": "50.00",
  "total_amount": "550.00"
}
```

Existing endpoints return decimal amounts according to the existing DRF renderer configuration; Flutter must tolerate the project's existing decimal representation. The gateway amount is always integer paise.

Common errors include malformed or unexpected request fields or a bad signature (`400`), invalid/missing customer authentication (`401`), an unauthorized role or approval restriction (`403`), an order outside the customer's ownership (`404`), a payment/order state conflict (`409`), throttling (`429`), an unverifiable provider response (`502`), and disabled/unavailable gateway configuration (`503`). The preparation and handover gates return `402` when verified payment is missing. Payment service errors use `{"code":"...","detail":"..."}`. Do not turn a network error into a paid state.

The three customer payment POST endpoints share a cache-backed `15/min` account throttle. A shared cache is needed for a shared limit across application workers; the throttle is approximate and does not replace ledger locks or event deduplication.

## Provider webhook

`POST /api/orders/payments/razorpay/webhook/`

This endpoint uses provider authentication rather than a customer JWT. Razorpay supplies `X-Razorpay-Signature` and `X-Razorpay-Event-Id`. The signature must authenticate the original raw request bytes before JSON is trusted. Duplicate event IDs are recorded without applying payment effects twice; a reused event ID with different payload bytes is rejected. Only explicitly handled lifecycle events may change payment state.

Subscribe to `payment.captured`, `order.paid`, `refund.processed` and `refund.failed`. A valid handled event returns `200 {"received":true}` only after its state changes, notification and deduplication record commit. An unsupported event with valid HMAC, event ID and string event name is acknowledged without requiring a lifecycle entity or changing state. A missing payment mapping returns `503` so the provider can retry; it is not silently discarded. Invalid signatures or a conflicting reused event identity return `400`. The service limits a webhook body to 65,536 bytes. Configure a matching request-size limit at the HTTP boundary.

A lost Flutter callback can be recovered through a valid webhook or customer reconciliation. Webhook delivery can be duplicated or arrive out of order; do not assume event delivery order. Rotation must retain older webhook verification secrets for retries. [Razorpay webhook validation](https://razorpay.com/docs/webhooks/validate-test/)

Do not log request bodies, signature headers, authorization headers, card details, UPI identifiers or complete provider responses. Django privacy annotations do not configure reverse proxies, load balancers, mobile telemetry or external APM; their logging rules require separate review.

## Ledger, concurrency and refunds

- `ShopPaymentAccount` stores the linked-account mapping without bank credentials.
- `OrderPayment` stores the server-owned amount/allocation snapshot and unique provider identifiers. `Order.payment_status` is the compatible summary, not independent proof.
- `PaymentWebhookEvent` records event identity and a payload digest rather than the full provider payload or customer payment details.
- Payment services use database transactions and the shared shop → order → payment lock order. Gateway HTTP calls occur outside row-locking transactions. These service checks do not make unrestricted direct database/ORM writes safe.
- Duplicate callbacks and webhooks must not create extra payment notifications, reserve stock again or complete an order again.
- Cancellation and rejection preserve existing inventory restoration and leave an explicit refund liability for captured payments. The order save signal queues the ledger state and schedules a best-effort attempt after the transaction commits. Gateway failure does not roll back the committed cancellation. A late capture for an already cancelled/rejected order must not revive it.
- Refund processing requests the full customer amount, including the ₹50 Platform fee, and the applicable Route reversal. A pending or uncertain refund is not reported as refunded.
- Ambiguous provider POST outcomes require reconciliation. A missing result in one provider query does not prove the earlier POST failed and is not permission to submit it again.
- Failed refund processing must preserve a durable pending/review state for operator follow-up. Unexpected partial refunds and failed refund events are held for review; a later capture replay cannot turn that state into an eligible paid order. Customer reconciliation can recover the same in-flight refund. There is no scheduled reconciliation worker or operator refund API in this source change; reviewed operational reconciliation and alerting remain prerequisites to real-money use.

## Sahil: Flutter integration work

No Flutter files are changed in this backend task. Required client work:

1. Display **Platform fee** using `platform_fee`, with `application_fee` as the legacy fallback. Display exactly one ₹50 charge; do not add delivery charges.
2. Keep order placement and Shop confirmation separate from payment. Show a Pay action after confirmation; retain Super Admin pending states for high-value orders.
3. Initiate checkout through the authenticated backend API and pass its order ID, key ID, amount and currency to Razorpay's supported Flutter checkout integration. Select a supported plugin version during the separately authorized Flutter task.
4. Send the checkout success identifiers/signature to the verification API. Do not set `paid` from the SDK event or a local UI flag.
5. Handle dismissals, pending capture, authentication expiry, connectivity loss and app restarts through backend status and reconciliation. Disable repeated submission while a request is in progress.
6. Show pending/refund/review states accurately. Do not display “refund completed” merely because an order was cancelled.
7. Continue displaying the customer-only handover OTP only when the backend permits it. The Shop Admin app must handle the verified-payment requirement before preparation and handover.
8. Keep checkout signatures and tokens out of screenshots, analytics, crash logs and persistent client storage wherever possible.

The official Flutter integration requires server order creation and backend signature verification; the public key and provider order ID are checkout inputs, not proof of payment. [Official Flutter integration](https://razorpay.com/docs/payments/payment-gateway/flutter-integration/standard/integration-steps/)

## Source review and future verification

The additive payment migration is [0010_payment_ledger.py](../orders/migrations/0010_payment_ledger.py), depending on `orders.0009_orderhandoverotp_handoverauditevent`. It creates the three new models and constraints only, contains no data backfill, and is **not applied**. The fee alias needs no column rename. Deploying preparation/OTP gates requires applying the reviewed migration in the same approved deployment; the new source must not be pointed at an unmigrated database. Source inspection does not establish migration safety, PostgreSQL concurrency correctness, gateway compatibility or production readiness.

Required isolated regression coverage includes disabled/missing/live key rejection; signature failures; account/order ownership; server-only amounts; high-value approval; duplicate success events; event ID/payload conflicts; wrong payment amount/currency/order; authorized versus captured state; cancellation/capture races; rollback on notification failures; ambiguous order/refund POSTs; full-refund state; unchanged inventory; historical order behavior; and Platform fee compatibility. Gateway transport tests must mock provider calls rather than charge anything.

Before authorizing isolated execution:

- Review the source and exact migration filename/dependencies.
- Complete the separately requested test infrastructure safety corrections and isolation approval. Do not assume the existing launcher is cleared for use.
- Approve a disposable PostgreSQL environment and independently verify its identity before migration/testing. Do not use the development cluster.
- Supply only fresh synthetic test secrets and verified Test Mode/linked-account configuration to that environment.
- Keep ordinary regression tests fully offline. Authorize sandbox network integration separately if desired.
- Approve any Flutter implementation separately.

Before considering live implementation, obtain the actual merchant/Route eligibility decision, settle onboarding and fee/tax/refund policies, implement and review operational payment/refund reconciliation and dispute handling, verify external telemetry redaction, and perform an additional security review. This document grants no permission to apply migrations, run tests, change credentials, activate live keys or collect real customer funds.

## Exact source files in this change

Created:

- `orders/razorpay_gateway.py`
- `orders/payments.py`
- `orders/payment_views.py`
- `orders/migrations/0010_payment_ledger.py`
- `orders/testing_payments.py`
- `orders/test_payments.py`
- `orders/test_razorpay_gateway.py`
- `docs/razorpay-payments.md`

Modified:

- `orders/models.py`
- `orders/urls.py`
- `orders/views.py`
- `orders/serializers.py`
- `orders/signals.py`
- `orders/staff_handover.py`
- `customers/serializers.py`
- `customers/views.py`
- `shop_admin/api_serializers.py`
- `shop_admin/api_views.py`
- `shop_admin/super_admin_views.py`
- `orders/test_staff_handover.py`
- `orders/test_handover_corrections.py`
- `orders/test_inventory_concurrency.py`
- `orders/tests.py`
- `shop_admin/tests.py`
- `shop_admin/test_super_admin_api.py`
- `FRONTEND_API_INTEGRATION.md`
- `SAHIL_FLUTTER_HANDOFF.md`
- `SHOP_ADMIN_API_CONTRACT.md`
- `SUPER_ADMIN_API_CONTRACT.md`

Verification performed: AST syntax parsing of the 24 changed Python files, with isolated Python and site initialization disabled. The two new test modules contain 52 test methods (37 payment/API/concurrency and 15 transport/signature cases), all **written but not run**. No Django startup, tests, migrations, databases, provider requests or Flutter execution occurred. Migration-model agreement has only been inspected in source.

The existing isolated launcher has a fixed test-label allowlist that does not yet include the new payment modules. Its previously identified safety corrections, a source review of the added labels, and explicit execution approval are prerequisites; do not run the current launcher to test this change.
