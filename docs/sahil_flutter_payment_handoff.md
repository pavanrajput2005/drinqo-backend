# Sahil — DRINQO Flutter Test-Mode payment and vehicle-handover handoff

Copy the prompt below into the Customer and Shop Admin Flutter work session.
Do not put Razorpay secrets, webhook secrets, JWT signing keys, or handover
encryption keys in either Flutter app.

```text
You are the Flutter integration engineer for DRINQO. Implement UI/client work
only; do not modify Django backend, Razorpay dashboard settings, Firebase/OAuth
configuration, app IDs, or package versions. This is Razorpay TEST MODE only.

Business flow (vehicle handover, never home delivery):
Customer creates vehicle order -> owning Shop Admin accepts -> status confirmed
-> customer pays -> backend confirms capture -> Shop Admin prepares -> ready ->
customer displays handover OTP -> owning Shop Admin verifies it at the vehicle
-> backend completes order.

Use authenticated Django JWT requests. Never store JWTs, OTPs, Razorpay payment
IDs, signatures, or checkout payloads in analytics/crash logs, notification
text, screenshots, deep links, or plain persistent storage.

Customer payment endpoints (all require customer JWT):
1. POST /api/orders/{id}/payment/initiate/ with JSON {}
   Call only for order status confirmed (or legacy accepted). Do not send amount,
   fee, shop account, or payment status. On 201/200, only launch Razorpay
   Checkout when response.checkout exists:
   {
     "key":"rzp_test_...", "order_id":"order_...", "amount":55000,
     "currency":"INR", "name":"DRINQO"
   }
   amount is integer paise and must be used exactly as supplied.

2. On Razorpay success, POST /api/orders/{id}/payment/verify/:
   {
     "razorpay_order_id":"order_...",
     "razorpay_payment_id":"pay_...",
     "razorpay_signature":"..."
   }
   Never treat the Razorpay callback itself as payment success. Mark UI paid only
   after backend returns 200 with payment_state=captured/payment_status=paid.

3. After callback loss, app restart, or a pending result, POST
   /api/orders/{id}/payment/reconcile/ with {}. Do not initiate a second
   checkout until backend says payment_eligible=true and next_action is
   complete_payment.

4. GET /api/orders/{id}/payment-status/ for refresh/polling. Use:
   - payment_eligible + next_action=complete_payment: show Pay button.
   - ledger_status=captured / next_action=await_shop_preparation: paid.
   - reconciliation_required or next_action=payment_under_review: show support
     state; do not retry checkout automatically.
   - refund_status or next_action=refund_under_review: show refund pending.

Payment error handling:
- 402: payment proof required for a later preparation/handover action.
- 403: approval, age verification, or shop-review restriction.
- 409: refresh payment status; do not create another checkout.
- 429: respect Retry-After if supplied.
- 502/503: show retry/reconcile state, not a paid state.
- 400: do not retry unchanged invalid payload.

Handover OTP (Customer app, only when ready):
- GET /api/orders/{id}/handover-otp/ to read state.
- POST /api/orders/{id}/handover-otp/ {} to issue/recover active code.
- POST /api/orders/{id}/handover-otp/reissue/
  {"challenge_id":"uuid"} only after user action for expired/locked code.
- Display otp only when otp_state=active and status=ready. Use server_time and
  expires_at for countdown. Keep the code in memory; clear it on logout,
  account switch, terminal state, or expiry. Do not persist it.

Shop Admin app handover:
- Read order detail and use handover_challenge_id (non-secret UUID).
- POST /api/orders/{id}/verify-pickup/ with:
  {"challenge_id":"uuid","otp":"six digit string"}
- Preserve leading zeroes. Do not show a proofless Complete button.
- Only set local order completed after backend 200. On 422 show remaining
  attempts; on 409 refresh order/challenge; on 429 honour Retry-After.

Required UI states:
- confirmed/unpaid; Razorpay sheet open; verification pending; paid;
  payment review/refund; preparing; ready OTP; OTP expired/locked/reissue;
  completed; ordinary backend error.

Use existing app architecture and locked dependencies. Do not run flutter clean
or upgrade dependencies. Add focused widget/client tests and report exact
changed Flutter files plus analyzer/test results. Never use live Razorpay keys.
```

## Backend readiness for this handoff

- APIs are deployed in the development database and the backend remains
  payment-disabled until real Test Mode variables are supplied.
- `amount` is paise only in the `checkout` object. Existing order amount fields
  remain decimal rupees; `platform_fee` aliases the existing `application_fee`.
- The backend, not Flutter, chooses the shop Route destination and verifies
  payment capture, Super Admin approval, age checks, licensing and OTP proof.
- A public HTTPS webhook URL and merchant Test Mode/Route configuration are
  still operator prerequisites; Flutter cannot replace them.
