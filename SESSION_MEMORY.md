# DRINQO project session memory

Snapshot: 2026-11-18. This is a starting point for future work, assembled from the backend source layout and existing handoff documents. It is not a complete conversation history or proof of current deployment status. Check the relevant current code, migrations and environment before changing behavior. Historical documents can describe superseded phases.

## Working preferences

- Explain progress and results in straightforward Marathi/Hinglish when talking to the owner.
- Apply the Ponytail approach: understand the existing flow and affected callers first, reuse project conventions, and make the smallest complete change. Preserve security, validation and necessary checks. The skill was read in this conversation, not installed.
- Preserve existing API contracts and data unless the requested change requires otherwise. Do not assume changes were deployed or Flutter was updated.

## Project map

- This workspace is the Django REST backend for DRINQO; customer and Shop Admin Flutter apps are discussed in handoffs but are not present in this workspace.
- `accounts/`: account, mobile authentication, identity providers and JWT lifecycle. `customers/`: customer profile, cart and related customer APIs. `products/` and `shops/`: catalog and shop data. `orders/`: checkout, order lifecycle, payments and handover. `shop_admin/`: shop and super-admin APIs. `config/urls.py` is the top-level route map; `/api/docs/` exposes API documentation.
- Dependencies are listed in `requirements.txt`; PostgreSQL-specific locking needs PostgreSQL tests, not just SQLite.
- This workspace did not expose Git metadata when this snapshot was made, so commit history and uncommitted changes could not be established.

## App flows and business rules documented

- Roles include Customer, Shop Admin and Super Admin. Google and Apple identity proofs are exchanged for Django JWTs; email OTP, password and session flows have dedicated endpoints. The final auth handoff says phone/SMS login is retired; contact numbers remain editable. Check `docs/final-auth-handover.md` and current routes before integrating; older OTP documents are historical.
- Customers build a server-side cart, preview checkout and create an order. `docs/outside-shop-pickup.md` describes walk-in and vehicle pickup outside the licensed shop, within 100 m at checkout; vehicle mode requires a customer-owned vehicle. The documented product subtotal minimum is ₹500, with a single ₹50 application/platform fee; totals of ₹15,000 or more require Super Admin approval. Prices, shop and quantities are server-derived.
- The documented order path is pending, optional Super Admin approval, Shop Admin confirmation, preparation, ready and staff handover/completion. Captured payment evidence gates later stages. The final authentication/handover document retires customer/staff handover OTP in favor of staff confirmation; older handover-OTP documents describe a prior phase. Verify current code and final handoff before implementation.
- Shop Admin manages its shop, catalog, inventory and orders; Super Admin handles platform oversight, shop applications, approvals and operational workflows. Exact contracts are in the documents below.
- Razorpay work is documented as Test Mode, with live merchant eligibility, settlement and fee funding unresolved. Do not infer live readiness or fake payment success. The Razorpay handoff includes older OTP language; reconcile it with the final handover contract and source.

## Outstanding verification and risks

- `docs/final-flutter-integration-handoff.md` lists remaining provider configuration, real-device/email delivery, Flutter integration, payment and end-to-end testing, deployment security and location/privacy decisions. Its recorded test results apply to that handoff, not automatically to the present environment.
- `BACKEND_ORDER_PLACEMENT_AUDIT.md` records potential or confirmed issues including stock concurrency, payment eligibility, profile DOB synchronization, refund recovery and idempotency. It predates later handoff changes in some areas; re-check each finding in current source before calling it open or fixed.
- `tools/isolated_tests/README.md` records source-predicted handover audit retention failures and a possible PostgreSQL lock-order risk, not observed test results. Their relevance after OTP retirement needs reassessment. Do not treat SQLite as evidence that PostgreSQL concurrency is safe.
- Client GPS and staff acknowledgments are not proof of physical presence. Production readiness, live payment eligibility and actual deployment/database state have not been established by this memory.

## Reference documents

- `docs/final-auth-handover.md` and `docs/final-flutter-integration-handoff.md`: latest named auth and Flutter handoffs, remaining work and validation history.
- `docs/outside-shop-pickup.md`: pickup modes and checkout behavior; check later final handoff for handover changes.
- `docs/razorpay-payments.md`, `docs/payment-backend-contract.md`, `docs/sahil_flutter_payment_handoff.md`: payment implementation, limitations and client integration.
- `BACKEND_ORDER_PLACEMENT_AUDIT.md`: detailed historical order audit and identified risks.
- `FRONTEND_API_INTEGRATION.md`, `SHOP_ADMIN_API_CONTRACT.md`, `SUPER_ADMIN_API_CONTRACT.md`, `SAHIL_FLUTTER_HANDOFF.md`: integration contracts; confirm against current source where documents differ.
