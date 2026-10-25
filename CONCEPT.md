# TechHub — Master's Project Concept

> **Thesis**: *"Design and Implementation of an Information System for Household Appliances Retail"*

## Current State (June 2026)

System fully functional. Microservices run via Docker Compose. All core retail features implemented. Bilingual (UA/EN) frontend and backend.

---

## 1. Architecture

### 1.1 Overview

```
                         ┌─────────────────────────┐
                         │        Users             │
                         │   (browser/ POS / admin) │
                         └─────────────┬─────────────┘
                                       │ HTTP
                                       ▼
                         ┌─────────────────────────┐
                         │  Gateway (Nginx + njs)  │
                         │      Port: 80           │
                         │  Route + Auth + CORS    │
                         └──┬───────┬───────┬──────┘
                            │       │       │
              ┌─────────────┘       │       └─────────────┐
              ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ Auth Service     │  │ Product Service  │  │  Frontend (Next) │
   │ (Express)        │  │ (Django/DRF)     │  │    Port: 3000     │
   │ Port: 3001       │  │ Port: 8000       │  │                  │
   │ DB: auth_db      │  │ DB: products_db  │  │  UA (default)    │
   │                  │  │                  │  │  EN variants     │
   │ Users/Sessions   │  │ Catalog          │  │  Storefront      │
   │ Roles/2FA/OAuth  │  │ Categories       │  │  Admin panel     │
   └──────────────────┘  │ Cart (session)   │  │  POS interface   │
                         │ Bilingual fields │  │                  │
                         └──────────────────┘  └──────────────────┘
          ┌──────────────────┐        ┌──────────────────┐
          │  Inventory       │        │   Order Service  │
          │  Service         │        │   (Django/DRF)   │
          │  (Django/DRF)    │        │   Port: 8002     │
          │  Port: 8001      │        │   DB: orders_db  │
          │  DB: inventory_db│        │                  │
          │                  │        │   Orders         │
          │  Warehouses      │        │   POS transactions│
          │  Stock levels    │◄──────►│   Revenue/Margin  │
          │  Stock movements │ HTTP   │   Daily sales     │
          │  Suppliers       │        │                  │
          │  Goods receipts  │        │                  │
          └──────┬───────────┘        └────────┬─────────┘
                 │                             │
                 │    ┌──────────────┐         │
                 ├────┤  RabbitMQ    │◄────────┤
                 │    │  Port: 5672  │         │
                 │    └──────┬───────┘         │
                 │           │                 │
                 ▼           ▼                 ▼
     ┌──────────────────┐ ┌──────────────────────────┐
     │  Product Consumer│ │  Notification Service    │
     │  (Django)        │ │  (Express + SMTP/Resend) │
     │  Port: N/A       │ │  Port: 8003              │
     │  DB: products_db │ │  DB: logging_db          │
     └──────────────────┘ │  Email + SSE + admin prefs│
                          └──────────────────────────┘
```

Not drawn: audit-service (Express, port 8005, `logging_db`), Redis (sessions, rate limits, SSE fan-out),
Prometheus + Grafana (scrape every service's `/metrics`).

### 1.2 Service Responsibilities

| Service | Stack | Responsible for | Communicates with |
|---------|-------|----------------|-------------------|
| **Gateway** | Nginx + njs | Entry point, routing, auth subrequest, RBAC, CORS | All services |
| **Auth Service** | Express + Better Auth | Users, sessions, roles, 2FA, OAuth (Google/GitHub) | — |
| **Product Service** | Django + DRF | Product catalog, categories, cart (session-based), bilingual fields | — |
| **Inventory Service** | Django + DRF | Warehouses, stock per location, stock movement audit, suppliers, Goods Receipt Notes | Product (product validation) |
| **Order Service** | Django + DRF | Orders (online + offline), order items, POS transactions, financial reports | Product (product info), Inventory (reserve/deduct) |
| **Frontend** | Next.js 16 | Storefront, admin panel, POS interface | Gateway (for all API) |

### 1.3 Communication

- **Synchronous HTTP** — real-time operations (stock reserve, deduct, release during checkout)
- **Async RabbitMQ events** — eventual consistency operations (stock sync, notifications, async reserve/release)

---

## 2. Authentication & Authorization

### 2.1 Auth Flow

```
1. Browser → Gateway → auth-service/auth/me (cookie-based subrequest)
2. Auth-service returns X-User-* headers + JSON
3. njs extracts X-User-* → conditionally blocks writes for unauthenticated
4. njs injects X-Gateway-User-* headers → downstream services
5. Downstream services trust headers via GatewayAuthentication
```

### 2.2 Route Protection

Routes processed by `nginx/auth.js` `checkAndProxy`:
- **Anonymous**: read-only, except cart routes and order creation
- **Authenticated**: role-based access
- **Admin**: full access
- **Cashier**: POS sales (not online orders)
- **Warehouse Worker**: goods receipts, purchase orders, stock movements and transfers

### 2.3 RBAC Matrix

| Resource | Anonymous | User | Cashier | Warehouse Worker | Admin |
|----------|:---------:|:----:|:-------:|:----------------:|:-----:|
| Catalog, warehouses, stock levels (read) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cart | ✅ | ✅ | ✅ | ✅ | ✅ |
| Online order (create) | ✅ | ✅ | ❌ | ❌ | ✅ |
| Pay for an order (`/api/orders/{id}/pay/`) | ✅ ¹ | ✅ ¹ | ❌ | ❌ | ✅ |
| Orders (view) | ❌ | own | own | own | all |
| Order status change | ❌ | ❌ | ❌ | ❌ | ✅ |
| POS sale | ❌ | ❌ | ✅ | ❌ | ✅ |
| Return / warranty claim (file) | ❌ | ✅ (own orders) | ❌ | ❌ | ✅ |
| Return / warranty claim (process) | ❌ | ❌ | ❌ | ❌ | ✅ |
| Review (write) | ❌ | ✅ ² | ❌ | ❌ | ✅ ² |
| Review moderation, promo codes | ❌ | ❌ | ❌ | ❌ | ✅ |
| Stock movements, adjust, transfer | ❌ | ❌ | ❌ | ✅ | ✅ |
| Suppliers (read) / GRN / purchase orders (draft, receive) | ❌ | ❌ | ❌ | ✅ | ✅ |
| Suppliers (write), purchase order send/cancel | ❌ | ❌ | ❌ | ❌ | ✅ |
| Catalog and category write | ❌ | ❌ | ❌ | ❌ | ✅ |
| Reports, users, audit log | ❌ | ❌ | ❌ | ❌ | ✅ |

¹ The signed-in owner, or a guest with the order's `pay_token`; anyone else gets 404.
² Only buyers who received the product (product-service asks order-service).

Measured on the running stack (gateway + services) with the seeded accounts, 2026-10-21.

---

## 3. i18n / Localization System

### 3.1 Architecture

- **Frontend**: `next-intl` v4.13 with `localePrefix: "always"`
- **URLs**: `/{locale}/...` — e.g., `/ua/products`, `/en/admin/summary`
- **Default locale**: `ua` (Ukrainian)
- **Secondary locale**: `en` (English)
- **Root `/`** redirects to `/ua`

### 3.2 Route Structure

```
/ua                    → UA home page
/en                    → EN home page
/ua/products           → UA product catalog
/en/products           → EN product catalog
/ua/admin/summary      → UA admin panel
/en/admin/summary      → EN admin panel
...same pattern for all routes
```

### 3.3 Implementation

- **Message files**: `app/messages/ua.json`, `app/messages/en.json` (~400 keys each)
- **Locale detection**: `proxy.ts` uses `createIntlMiddleware` from `next-intl/middleware`
- **Navigation**: locale-aware `Link`, `useRouter`, `usePathname` from `~/i18n/navigation` (wraps `next-intl/navigation`)
- **Provider**: `NextIntlClientProvider` in `[locale]/layout.tsx`
- **Location**: i18n config lives in `app/i18n/` (routing.ts, navigation.ts, request.ts)
- **Metadata**: `generateMetadata` in `[locale]/layout.tsx` returns locale-specific `<title>`

### 3.4 Backend Bilingual Fields

- **Product**: `name_uk`/`name_en`, `description_uk`/`description_en`
- **Category**: `name_uk`/`name_en`, `description_uk`/`description_en`
- **Pricing**: UAH (default), USD when `Accept-Language: en`. Rate: live from exchangerate-api.com, cached 1 h
  (`products/currency.py`); fallback 0.0241 USD per 1 UAH. The `UAH_TO_USD_RATE` env var pins the rate and is
  also **USD per 1 UAH** (e.g. `0.0241`, not `41.5`)
- **Serializer**: `to_representation()` reads `Accept-Language` header, falls back to `name`/`description`
- **Gateway**: passes `Accept-Language` through to backend

### 3.5 Language Switcher

- **Admin sidebar**: UA/EN toggle buttons using `window.location.href`
- **Storefront header**: UA/EN toggle buttons in right-side actions area
- **Pattern**: `window.location.href = \/${lang.code}${pathname}` where `pathname` comes from locale-aware `usePathname` (strips locale prefix)

---

## 4. Business Processes

### 4.1 Goods Receipt (Inbound)

```
Supplier → Delivery → GRN creation → Stock added to warehouse
```

| Step | Action | Service |
|------|--------|---------|
| 1 | Worker creates Goods Receipt Note | Inventory |
| 2 | Stock added (qty + cost_price), weighted-average cost recalculated | Inventory |
| 3 | `inventory.stock.changed` published | → RabbitMQ |

### 4.1a Purchasing (before the goods arrive)

| Step | Action | Service |
|------|--------|---------|
| 1 | Staff sets a reorder point and batch per stock line (warehouse page) | Inventory |
| 2 | Reorder suggestions list lines at or below their point, net of what is already ordered | Inventory |
| 3 | Drafts are created per supplier and warehouse; an admin sends them | Inventory |
| 4 | The delivery is booked as a GRN against the order (partial deliveries allowed, over-delivery rejected) | Inventory |

### 4.2 Internal Transfer

```
Central warehouse → Transfer → Showroom
```

| Step | Action | Service |
|------|--------|---------|
| 1 | Worker creates transfer between warehouses | Inventory |
| 2 | Source stock decreased, destination stock increased | Inventory |
| 3 | Entry logged in `StockMovement` (type: `transfer`) | Inventory |

### 4.3 Online Sale

```
Customer → Browse → Cart → Checkout → Reserve → Ship → Deduct
```

| Step | Action | Service |
|------|--------|---------|
| 1 | Browse catalog, add to cart | Product |
| 2 | Checkout (order create) | Order (via Gateway) |
| 3 | Order created with status `unpaid` | Order |
| 4 | Order calls Inventory → **reserve** stock | Inventory (HTTP) |
| 5 | `order.created` published | → RabbitMQ |
| 6 | After payment (`paid`) admin changes status to `delivering` | Order |
| 7 | Order calls Inventory → **deduct** stock | Inventory (HTTP) |
| 8 | `inventory.stock.changed` published | → RabbitMQ |
| 9 | Status → `delivered` → `completed` | Order |

### 4.4 Offline Sale (POS)

```
Customer in showroom → Cashier via POS → Search → Sale → Deduct
```

| Step | Action | Service |
|------|--------|---------|
| 1 | Cashier searches product in POS | Frontend → Product |
| 2 | Cashier adds items to POS receipt, enters buyer info | Frontend |
| 3 | Cashier confirms sale | Order (via Gateway) |
| 4 | Order created: `channel=offline`, `status=unpaid` | Order |
| 5 | Order calls Inventory → **deduct_batch** from showroom (all items or none; on failure the sale is cancelled) | Inventory (HTTP) |
| 6 | On success the sale is `completed` with `payment_status=paid` (paid at the till) | Order |

### 4.5 Returns and Warranty Claims

| Step | Action | Service |
|------|--------|---------|
| 1 | Buyer files a claim on a delivered line: return (14 days) or warranty (`warranty_months` from the sale) | Order |
| 2 | Admin approves or rejects (with a comment) | Order |
| 3 | Item received; a return goes back on stock → **return_to_stock** | Inventory (HTTP) |
| 4 | Resolved: refund (Stripe partial refund / cash), replacement (**deduct_batch**) or repair | Order, Inventory |

### 4.6 Order Cancellation

| Step | Action | Service |
|------|--------|---------|
| 1 | Admin sets status to `cancelled` | Order |
| 2 | Order calls Inventory → **release** reserved stock | Inventory (HTTP) |
| 3 | `order.cancelled` published | → RabbitMQ |

---

## 5. Data Architecture

### 5.1 Product Service (products_db, PostgreSQL)

| Entity | Description |
|--------|-------------|
| `Category` | Hierarchical categories. Bilingual fields: `name_uk`/`name_en`, `description_uk`/`description_en` |
| `Product` | Product with bilingual fields (`name_uk`/`name_en`, `description_uk`/`description_en`), price in UAH, brand, color, specs, features, image, `stock` (synced from inventory events), `warranty_months`, `rating` + `review_count` (computed from reviews) |
| `Review` | One per user and product, rating 1–5 and text, written only by buyers who received the product; admin can hide it |
| `Cart` | Session-based cart (UUID session_id or user_id). No auth required |
| `CartItem` | Cart item (product + quantity). Merge endpoint for anonymous→user |

### 5.2 Inventory Service (inventory_db, PostgreSQL)

| Entity | Description |
|--------|-------------|
| `Warehouse` | Warehouse/showroom. Name, type (`warehouse`/`showroom`), address, active |
| `Stock` | Stock level per product per warehouse (`quantity`, `reserved`, weighted-average cost, `reorder_point`, `reorder_quantity`). Unique: `(product_id, warehouse_id)` |
| `StockMovement` | Audit log. Types: `receipt`, `transfer`, `sale`, `adjustment`, `write_off`, `reserve`, `release`, `deduct`, `return` |
| `Supplier` | Supplier name, contact person, phone, email, address |
| `PurchaseOrder` / `PurchaseOrderItem` | Order to a supplier: `draft` → `sent` → `partially_received` → `received` (or `cancelled`); items with expected cost and received quantity |
| `GoodsReceiptNote` | GRN. Supplier, warehouse, date, notes, author, optional purchase order it fulfils |
| `GoodsReceiptItem` | GRN item. Product, qty, cost_price. On create: adds to `Stock.quantity` and recomputes the average cost |
| `ActivityEvent` | Admin activity feed entries |

### 5.3 Order Service (orders_db, PostgreSQL)

| Entity | Description |
|--------|-------------|
| `Order` | Order with auto-number, channel (`online`/`offline`), status (`unpaid`→`paid`→`delivering`→`delivered`→`completed`/`cancelled`), customer data, `total_amount` (goods minus discount), promo code + `discount_amount`, payment status, `delivered_at` |
| `OrderItem` | Order line. Product, qty, price (from the catalog at sale), cost_price (inventory average cost at write-off), `warranty_months` (catalog snapshot) |
| `OrderStatusHistory` | One row per status change, for the fulfilment funnel and time between stages |
| `ReturnRequest` | Return (14 days) or warranty claim for units of one line: `submitted` → `approved` → `received` → `resolved` (refund / replace / repair) or `rejected` |
| `PromoCode` | Percent or fixed discount with optional dates, minimum order, use limit and once-per-customer |

### 5.4 Auth Service (auth_db, PostgreSQL)

Managed by Better Auth:
- `user`: email, name, role, status
- `session`: token, IP, user agent, expiry
- `account`: linked OAuth accounts
- `verification`: email/2FA codes

### 5.5 Audit and Notification Services (logging_db, PostgreSQL)

Deliberate compromise against database-per-service: audit-service (`audit_logs`) and
notification-service (`notifications`, preferences) share `logging_db`. Both are append-mostly logs and
never touch each other's tables. User and admin lookups go to auth-service over HTTP, not to `auth_db`.

---

## 6. Event-Driven Integration (RabbitMQ)

### 6.1 Event Catalog

| Event | Publisher | Consumers | Description |
|-------|-----------|-----------|-------------|
| `inventory.stock.changed` | Inventory | Product, audit | Stock level changed → sync Product.stock |
| `inventory.goods_received` | Inventory | Audit | GRN created (Product.stock follows the `inventory.stock.changed` it also publishes) |
| `inventory.low_stock` | Inventory | Notification, audit | Stock below threshold → email + SSE alert |
| `order.created` | Order | Notification, audit | New order → confirmation email (stock is reserved over HTTP, not by this event) |
| `order.cancelled` | Order | Notification, audit | Order cancelled → notify customer (stock is released over HTTP) |
| `order.status_changed` | Order | Notification, audit | Status transition → notify customer |

audit-service binds `#`, so it stores every event.

### 6.2 Saga Pattern

Critical multi-service operations use Saga:
```
1. Order Service: Create order (status=unpaid)
2. Order → Inventory: Reserve stock (HTTP with idempotency_key)
3. If success → order confirmed
4. If failure → Order: Cancel order (compensating action)
```

---

## 7. API Design

### 7.1 Gateway Routes

```
/api/inventory/*     → inventory-service:8001
/api/orders/*        → order-service:8002
/api/reports/*       → order-service:8002
/api/notifications/* → notification-service:8003
/api/cart/*          → product-service:8000
/api/*               → product-service:8000
/media/*             → product-service:8000
/auth/*              → auth-service:3001
/*                   → frontend:3000
```

### 7.2 Product Service (`/api/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/products/` | Product list (filtering, pagination, locale-aware) |
| GET | `/api/products/{id}/` | Product detail (locale-aware) |
| POST | `/api/products/` | Create product |
| GET | `/api/categories/` | Category list (locale-aware) |
| GET | `/api/cart/` | Cart contents (by session or user) |
| POST | `/api/cart/add_item/` | Add item |
| POST | `/api/cart/merge/` | Merge anonymous cart into user cart |

### 7.3 Inventory Service (`/api/inventory/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inventory/warehouses/` | Warehouse list |
| POST | `/api/inventory/warehouses/` | Create warehouse |
| GET | `/api/inventory/warehouses/{id}/stock/` | Stock by warehouse |
| GET | `/api/inventory/stock/` | Stock query (filter: `?warehouse_id=&product_id=`) |
| POST | `/api/inventory/stock/reserve/` | Reserve stock (internal) |
| POST | `/api/inventory/stock/deduct/` | Deduct stock (internal) |
| GET | `/api/inventory/stock/movements/` | Stock movement log |
| GET | `/api/inventory/suppliers/` | Supplier list |
| POST | `/api/inventory/suppliers/` | Create supplier |
| GET | `/api/inventory/goods-receipts/` | GRN list |
| POST | `/api/inventory/goods-receipts/` | Create GRN |

### 7.4 Order Service (`/api/orders/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/orders/` | Order list (filter: `?status=&channel=`) |
| POST | `/api/orders/` | Create order (checkout/POS) |
| GET | `/api/orders/{id}/` | Order detail |
| PATCH | `/api/orders/{id}/status/` | Update status |
| GET | `/api/orders/my/` | Current user's orders |
| POST | `/api/orders/pos/` | POS sale |
| GET | `/api/reports/sales/` | Sales report |
| GET | `/api/reports/revenue/` | Revenue & margin report |
| GET | `/api/reports/inventory-value/` | Inventory valuation |

---

## 8. Frontend

### 8.1 Stack

- **Next.js 16** (App Router) + React 19
- **Tailwind CSS v4** + shadcn/ui (Radix primitives)
- **Framer Motion** (animations)
- **next-intl v4.13** (i18n)
- **Lucide React** (icons)
- **Geist** (font)
- Dark/light theme

### 8.2 Route Structure (all under `/[locale]/`)

| Route | Purpose |
|-------|---------|
| `/` → `/ua` | Home (hero, categories, features, testimonials, CTA) |
| `/products` | Product catalog with filtering |
| `/checkout` | Checkout flow (cart → payment) |
| `/my/orders` | Customer orders (`/orders` redirects here) |
| `/admin/summary` | Admin dashboard (stats, health) |
| `/admin/products` | Product CRUD |
| `/admin/categories` | Category management |
| `/admin/orders` | Order management |
| `/admin/pos` | POS terminal |
| `/admin/reports` | Sales reports |
| `/admin/warehouses` | Warehouse management |
| `/admin/suppliers` | Supplier management |
| `/admin/stock-movements` | Stock movement log |
| `/admin/goods-receipts` | GRN management |
| `/admin/users` | User management |
| `/admin/returns` | Returns and warranty claims |
| `/admin/promo-codes` | Promo codes |
| `/admin/purchase-orders` | Reorder suggestions and purchase orders |
| `/compare` | Side-by-side product comparison |
| `/my/orders/{id}` | Order detail with return / warranty claims |

### 8.3 Language Structure

- `proxy.ts`: i18n middleware + auth proxy (Next.js 16, replaces middleware.ts)
- `app/i18n/routing.ts`: locale config (ua, en; prefix: always)
- `app/i18n/navigation.ts`: locale-aware Link, redirect, usePathname, useRouter
- `app/i18n/request.ts`: message loading
- `app/messages/ua.json`, `app/messages/en.json`: translation files
- `app/[locale]/layout.tsx`: NextIntlClientProvider + generateMetadata
- Each page component uses `useTranslations()` (client) or `getTranslations()` (server)

---

## 9. Deployment

### 9.1 Docker Compose

```
Services:
  gateway           :80 (Nginx + njs)
  frontend          :3000 (Next.js)
  auth-service      :3001 (Express)
  product-service   :8000 (Django)
  inventory-service :8001 (Django)
  order-service     :8002 (Django)
  
  products_db       :5432 (PostgreSQL)
  auth_db           :5433 (PostgreSQL)
  inventory_db      :5434 (PostgreSQL)
  orders_db         :5435 (PostgreSQL)
  logging_db        :5436 (PostgreSQL, audit + notifications)
  
  rabbitmq          :5672 (AMQP), :15672 (mgmt)
  redis             :6379
  ...

Network: microservices_network (bridge)
```

### 9.2 Key Nginx Config Points

- Route order: prefix matches must come before `/{path}`
- `/auth-check` internal location: auth-service unreachable → API requests fail with 500 (fail-closed); its 4xx/5xx answers map to 200 = anonymous
- All API routes go through `auth_request /auth-check` + `js_content auth.checkAndProxy`
- CORS: njs-driven `checkOrigin`, server-level `add_header` for all responses
- WebSocket support for frontend HMR
- `proxy_redirect` rewrites internal service URLs to public

---

## 10. Current Status

### Done ✅

| Domain | Status |
|--------|--------|
| Gateway (Nginx + njs) | ✅ Fully functional |
| Auth Service (Express + Better Auth) | ✅ Users, sessions, roles, 2FA, OAuth |
| Product Service (Django/DRF) | ✅ Catalog, categories, cart, bilingual fields |
| Inventory Service (Django/DRF) | ✅ Warehouses, stock, movements, suppliers, GRN, events |
| Order Service (Django/DRF) | ✅ Orders, POS, reports, revenue/margin, Saga |
| RabbitMQ Integration | ✅ Events, consumers, deduplication |
| Frontend Storefront | ✅ Pages, catalog, cart, checkout, auth |
| Frontend Admin Panel | ✅ Products, orders, POS, reports, users, warehouses, suppliers, GRN, stock movements |
| Frontend i18n | ✅ UA/EN routes, message files (~400 keys), language switcher, bilingual backend fields |
| Agent Rules (AGENTS.md) | ✅ Context per service, test-as-spec rule |
| Pricing trust boundary | ✅ Prices from the catalog, cost of goods from inventory at write-off |
| Analytics | ✅ KPIs with period comparison, daily/weekly/monthly trend, top products, ABC, fulfilment funnel, stock health, CSV export |
| Returns & warranty | ✅ 14-day returns, warranty claims, restock, Stripe partial refunds |
| Reviews | ✅ Buyers-only reviews, rating computed from them, moderation |
| Comparison | ✅ Up to 4 products side by side |
| Purchasing | ✅ Reorder points, suggestions, purchase orders received through GRNs |
| Promo codes | ✅ Percent / fixed, limits, once per customer, Stripe coupon |
| Monitoring | ✅ Prometheus metrics in every service, Grafana dashboard |

### Known Issues 🐛

| Issue | Notes |
|-------|-------|
| `orders_db` test DB leftover | Fixed: run `manage.py test --noinput` (the `autoclobber` prompt raised `EOFError`) |
| Backend tests assertion count | Fixed: order tests that accepted either outcome now stub inventory and assert one result |
| Cart in product-service, orders in order-service | Checkout spans two services; no distributed transaction |
| POS sales made before POS sales were completed at the till | Fixed: migration `orders.0009` marks unpaid offline orders `completed` + paid as of their creation time |
| Dev compose published service and database ports on all interfaces | Fixed: dev binds every port except the gateway (80) to `127.0.0.1`, so auth-service `/api/internal/*` (no auth) and PostgreSQL are reachable only from the dev machine; prod publishes only the gateway and Grafana on loopback |
| Stripe not exercised end to end locally | Without `STRIPE_SECRET_KEY` checkout stops at "Payment session creation failed"; run the payment, coupon and partial refund paths on staging with test keys |
| Carrier delivery is not charged online | By design: the customer pays the carrier on receipt; the order total and Stripe charge cover goods only |

---

## 11. Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| **Runtime** | Python 3.13 (Django services), Node.js 24 LTS (Auth, Audit, Notification, Frontend) |
| **Frameworks** | Django 5 + DRF, Next.js 16, Express 4 |
| **Database** | PostgreSQL 15 (5 instances) |
| **Cache** | Redis 7 |
| **Message broker** | RabbitMQ 4.1 |
| **Gateway** | Nginx + njs (auth plugin) |
| **Frontend** | React 19, Tailwind CSS v4, shadcn/ui, Framer Motion |
| **i18n** | next-intl v4.13 |
| **Auth** | Better Auth |
| **Payments** | Stripe (webhook integration) |
| **Notifications** | SMTP (e.g. Gmail) or Resend (email), SSE, RabbitMQ events |
| **Monitoring** | Prometheus 3.5 (metrics from every service), Grafana 12 (TechHub overview dashboard) |
