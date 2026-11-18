# DRINQO payment backend contract

## Compliance and activation

This source is disabled-by-default Razorpay Test Mode integration only. Live use
for alcohol orders is prohibited unless DRINQO first receives written provider
authorization for the exact licensed-shop, vehicle-handover marketplace and
Route settlement model, plus applicable legal and excise review. No endpoint
permits a client to choose an amount, settlement account, or paid state.

## Customer APIs

All customer endpoints require a customer JWT and order ownership.

| Method and endpoint | Request | Success | Notes |
| --- | --- | --- | --- |
| `POST /api/orders/{id}/payment/initiate/` | `{}` | `201`/`200` payment checkout record | Server supplies integer paise amount and Test Mode checkout inputs. |
| `POST /api/orders/{id}/payment/verify/` | `razorpay_order_id`, `razorpay_payment_id`, `razorpay_signature` | `200` captured projection | Callback is not proof; provider capture is rechecked. |
| `POST /api/orders/{id}/payment/reconcile/` | `{}` | `200` current projection | Recover lost callbacks; does not create a second charge. |
| `GET /api/orders/{id}/payment-status/` | none | legacy totals plus ledger fields | `ledger_status`, `refund_status`, `reconciliation_required`, `payment_eligible`, `next_action`. |

For ₹500 subtotal plus ₹50 platform fee, the authoritative total is ₹550 or
`55000` paise. The ₹15,000 approval rule uses the existing server-side total.

## Super Admin payment operations

All routes require an active Super Admin JWT:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/super-admin/payments/{payment_uuid}/` | Safe ledger, refund, and case projection; no gateway IDs, secrets, bank data, or webhook body. |
| `GET /api/super-admin/payments/cases/?kind=&status=` | Reconciliation, refund, settlement, and dispute work queue. |
| `POST /api/super-admin/payments/{payment_uuid}/cases/` | Open an audited settlement, dispute, refund, or reconciliation case with `kind`, `reason_code`, and optional non-PII `external_reference`. |
| `POST /api/super-admin/payments/{payment_uuid}/refund/retry/` | Audited retry request for `refund_required`, `refunding`, or `review`; provider confirmation is still required before refund success. |

The retry endpoint returns `400` for an ineligible ledger state, `401/403` for
authentication/role failure, and never reports `refunded` before a confirmed
provider outcome. Gateway errors leave durable review state for follow-up.

## Operational reconciliation

`python manage.py reconcile_payments` is read-only by default and only reports
records requiring attention. `--execute` is an operator action requiring a
separately approved provider/legal runbook. It must not run against an
unverified database or while provider authorization is absent.

Migration `orders.0011_payment_operations` is additive and depends on
`orders.0010_payment_ledger`. It creates refund-attempt and operational-case
records only; it does not rewrite historical orders, payments, refunds, or
settlement data. Apply it only after approved isolated PostgreSQL validation.

## Flutter handoff

Sahil should preserve the backend order-confirmation → payment → preparation
sequence, show one ₹50 Platform fee, submit no amount, and use the backend
payment-status response after callback/app restart. Do not expose secrets,
signatures, OTPs, or gateway IDs to analytics. Provider-dependent behavior is
provisional until isolated tests and provider authorization are complete.
