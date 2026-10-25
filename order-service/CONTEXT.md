# Order Service

## Stack
- **Runtime:** Python 3.13 (slim in Docker)
- **Framework:** Django 5.2 + DRF 3.18
- **Database:** PostgreSQL 15 (`orders_db`, port 5435)
- **Auth:** `GatewayAuthentication` — reads `X-Gateway-User-*` headers, syncs local Django User
- **Message Broker:** RabbitMQ (publisher)
- **Payments:** Stripe webhook integration
- **Docs:** drf-spectacular (Swagger, ReDoc)

## Port
- Container: 8002 (default)
- Docker compose: maps to 8002:8002

## Quick Start
```bash
python manage.py runserver 0.0.0.0:8002
python manage.py test --noinput
```

## Apps

### `orders/`

#### Models
- **Order** — core entity. Fields: `order_number` (timestamp + random suffix), `channel` (`online`/`offline`), `status` (see state machine), customer data (`customer_name`, `customer_email`, `customer_phone`, `customer_address`), `total_amount`, `created_by`, `notes`.
- **OrderStatusHistory** — one row per status change (`from_status`, `to_status`, `changed_by`, `changed_at`), written by `record_status_change` in `update_order_status`, the Stripe paid webhook and POS completion. Orders older than migration 0006 have no rows.
- **OrderItem** — order line: product, quantity, price_at_sale, cost_price_at_sale (for margin calculation).

### Status Machine
```
unpaid → paid → delivering → delivered → completed
  ↓       ↓        ↓             ↓
cancelled cancelled cancelled  cancelled
```
- `unpaid`: initial state (created, not paid yet)
- `paid`: payment confirmed (stock reserved via Stripe webhook)
- `delivering`: stock deducted, in transit
- `delivered`: received by customer
- `completed`: fully processed
- `cancelled`: from any state (triggers stock release via inventory_service.release_stock)

Transitions enforced in `Order.can_transition_to()`.

#### Viewsets
- `OrderViewSet` — CRUD, my orders (`@action`), POS sales (`@action`), status updates.
- `ReportViewSet` — sales report, revenue/margin report, inventory-value report.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/orders/` | Admin | Order list (filter: `?status=&channel=`) |
| POST | `/api/orders/` | Any auth | Create order (online checkout) |
| GET | `/api/orders/{id}/` | Any auth | Order detail |
| PATCH | `/api/orders/{id}/` | Admin | Update order status |
| GET | `/api/orders/my/` | Auth user | Current user's orders |
| POST | `/api/orders/{id}/pay/` | Buyer / Admin | Stripe checkout URL. The signed-in owner, staff, or a guest sending `{token}` = `pay_token` from the create response (7 days); anyone else gets 404 |
| POST | `/api/orders/pos/` | Cashier/Admin | POS sale (offline, immediate deduct, ends `completed` + paid) |
| POST | `/api/orders/stripe_webhook/` | Stripe signature | Stripe payment callback; public URL `/api/payments/webhook` (gateway rewrite) |
| GET | `/api/reports/sales/` | Admin | KPIs (revenue, orders, AOV, margin, cancellation rate) + `previous` period of equal length when `from` and `to` are both set (`?from=&to=`, ISO dates, `to` inclusive) |
| GET | `/api/reports/top-products/` | Admin | Best sellers (`?from=&to=&sort=revenue\|quantity\|margin&limit=10`, max 100), with revenue share |
| GET | `/api/reports/revenue/` | Admin | Revenue & margin (same numbers as sales, kept for dashboard cards) |
| GET | `/api/reports/daily-sales/` | Admin | Revenue + orders per `?group=day\|week\|month` (weeks start Monday), zero-filled (`?from=&to=`, default last 30 days, max 3 years) |
| GET | `/api/reports/abc/` | Admin | ABC analysis by cumulative revenue (A ≤80%, B ≤95%, C rest), per class totals (`?from=&to=`) |
| GET | `/api/reports/fulfillment/` | Admin | Funnel, payment conversion, cancellations by stage, median/avg hours between stages (`?from=&to=&channel=online`) |
| GET | `/api/reports/inventory-value/` | Admin | Stock at weighted-average cost (proxies inventory-service `stock/valuation/`) |
| GET | `/api/orders/promo-check/?code=&subtotal=` | Any (10/min per client) | Preview a code's discount |
| GET/POST/PATCH | `/api/orders/promo-codes/` | Admin | Manage codes (no delete: deactivate) |
| GET | `/api/orders/purchased/?product_id=` | Auth | `{purchased}`: the user has a delivered/completed order with it (gates reviews) |
| GET/POST | `/api/orders/returns/` | Auth (own) / Admin (all) | Returns and warranty claims (`?status=&kind=&order=`); POST `{item_id, kind, quantity, reason}` |
| POST | `/api/orders/returns/{id}/status/` | Admin | `{status, resolution?, restock?, comment?}` |
| GET | `/api/docs/` | Any | Swagger UI |
| GET | `/api/redoc/` | Any | ReDoc |

## Promo codes (`promo.py`)
- `PromoCode`: `percent` or `fixed`, optional window, `min_order_amount`, `max_uses`, `once_per_customer`.
- `promo_code` on online and POS create; evaluated on the catalog subtotal under a row lock, use counted in
  the order's transaction, given back when the order is cancelled. Discount is capped at the subtotal.
- `Order.discount_amount`; `total_amount` is net of it. Stripe gets catalog-priced lines plus a one-off coupon.
- Reports: totals and daily/channel revenue are net of discounts (`total_discount` in sales); per-product
  reports (top, ABC) are before discounts. Refunds share the order discount by line value.

## Returns and warranty (`returns.py`)
- Claimable once the order is `delivered`/`completed`. Periods start at `Order.delivered_at`
  (set on → delivered, and at a POS sale; older orders fall back to `updated_at`).
- `return`: 14 days. `warranty`: `OrderItem.warranty_months` (catalog snapshot at sale, 12 if missing).
- `submitted → approved → received → resolved`, or `→ rejected` (needs a comment; not after a restock).
- received: `restock` (default for returns) calls inventory `stock/return_to_stock/`.
- resolved: `refund` (Stripe partial refund with idempotency key, or a recorded cash payout for POS),
  `replace` (deducts a new unit), `repair` (warranty only; frees the units for a later claim).
- Side effects run before the save, so a failed refund or stock call leaves the claim unchanged.
- Guest (anonymous) orders cannot be claimed online: there is no account to own the claim.

## Pricing (trust boundary)
- Item `price` and `product_name` are always taken from product-service on create (online and POS);
  a client-sent `price` is ignored and `cost_price` is not accepted. Stripe charges these server prices.
- `cost_price` is written when stock is deducted (POS sale, shipping) from inventory's `unit_costs`
  (weighted-average cost); until then it is 0, so paid-but-unshipped orders show full margin.
- `shipping_cost` must be ≥ 0.

## Saga Integration (Order ↔ Inventory)

### Synchronous HTTP Flow
0. **Which warehouse:** pickup and POS name it (`warehouse_id`); a delivery order gets one from the server
   (`DEFAULT_FULFILMENT_WAREHOUSE_ID`, else the first active `warehouse` holding every item), or is refused
   with 400 when none has it all (503 if inventory is down). Online pickup without a showroom is refused.
   Every call carries `X-Internal-Token` (`INTERNAL_SERVICE_TOKEN`), so guests and the Stripe webhook can move stock.
1. **Create order:** order-service → `POST /api/inventory/stock/reserve/` (with `product_id`, `quantity`, `warehouse_id`)
2. **Ship order:** order-service → `POST /api/inventory/stock/deduct_batch/` with `from_reserved=true` (converts reserve → deduction, all items or none)
3. **Cancel order:** order-service → `POST /api/inventory/stock/release/` (releases reserved stock)

### Saga Integration (Reserve on Create)
- **Create order:** stock reserve with saga compensation on failure (rollback + cancel order)
- **Ship order / POS sale:** one batch deduct for all items; on failure nothing was written off, so only the status is reverted (shipping) or the sale cancelled (POS)
- **Cancel order:** stock release via `release_stock` (best-effort, idempotency keys prevent double-release)
- **Idempotency keys:** all inventory client calls include `idempotency_key` for safe retry
- **Stock pre-check:** `check_availability` before order creation (returns 400 if insufficient)

## RabbitMQ Integration

### Publisher
- `order.created` — published after order creation (consumed by notification-service)
- `order.cancelled` — published on cancellation (stock is released over HTTP, not by this event)
- `order.status_changed` — published on any status transition (consumed by notification-service)
- **Exchange:** `techhub.events` (topic)

## Reports
- **Sold = `paid`, `delivering`, `delivered`, `completed`** (`SOLD_STATUSES`) in every report, by `created_at`
- **Revenue = goods only** (`price × quantity` of items); shipping in `total_amount` is excluded
- Aggregated in SQL; invalid `from`/`to` returns 400
- **Inventory value:** inventory-service values on-hand stock at `average_cost`; stock without a GRN has cost 0 (`uncosted_quantity`)

## Auth & Permissions
- `GatewayAuthentication` — base class for all Django services
- `IsAdminUser` — for status changes, reports
- `IsAuthenticatedOrReadOnly` — for order creation (anonymous checkout allowed)
- `IsAuthenticated` on retrieve (filtered by created_by for non-staff)

## Tests
- **Framework:** Django `TestCase` + `APITestCase`
- **Coverage:** models, status transitions, API (create, auth gating, status update, my orders, POS, reports)
- **Inventory calls:** stubbed with `unittest.mock` (tests do not need a running inventory-service)
- **Run:** `python manage.py test --noinput` (without `--noinput` a leftover test DB triggers the
  `autoclobber` prompt, which fails with `EOFError` in a non-interactive shell)

## Known Issues
- Unknown products return 400, and a product-service outage returns 503 (fail closed, `product_client.py`)
- `order_number` uniqueness is enforced by the DB (`unique=True` since migration 0001); `create_order` retries up to 3 times on `IntegrityError`
