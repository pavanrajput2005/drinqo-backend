# DRINQO Backend Project Structure

This describes the backend workspace as inspected on 2026-11-18. Flutter app source is not present in this workspace.

```text
drinqo_backend/
├── config/                 Django settings and main API routing
├── accounts/               Accounts, mobile authentication and JWT
├── customers/              Customer profile, cart and customer APIs
├── products/               Products and catalog
├── shops/                  Shops, permissions and location
├── orders/                 Checkout, orders, payments and handover
├── shop_admin/             Shop Admin and Super Admin APIs
├── docs/                   Authentication, Flutter, payments and pickup handoffs
├── tools/                  Test and supporting tools
├── firebase/               Firebase-related files
├── private_shop_licenses/  Private shop license files
├── backups/                Available backups
├── manage.py               Django management entry point
├── requirements.txt        Python dependencies
├── SESSION_MEMORY.md       Project context and working preferences
├── FRONTEND_API_INTEGRATION.md
├── SHOP_ADMIN_API_CONTRACT.md
├── SUPER_ADMIN_API_CONTRACT.md
└── BACKEND_ORDER_PLACEMENT_AUDIT.md
```

## Main flow

```text
Customer login and profile
  → Shop and products
  → Cart
  → Checkout preview and order creation
  → Super Admin approval, when required
  → Shop Admin confirmation
  → Payment
  → Preparing → Ready → Staff handover
```                         

See `SESSION_MEMORY.md` for business rules, outstanding verification and links to detailed contracts. Confirm behavior in current source before making changes; historical handoffs may describe superseded flows.
