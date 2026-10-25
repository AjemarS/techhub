# Product Service

## Stack
- **Runtime:** Python 3.13 (slim in Docker)
- **Framework:** Django 5.2 + DRF 3.18
- **Database:** PostgreSQL 15 (`products_db`, port 5432)
- **Auth:** `GatewayAuthentication` — reads `X-Gateway-User-*` headers, syncs local Django User
- **Media:** Static/media files served by the service (Django `serve` in dev, Nginx proxies in prod)
- **Docs:** drf-spectacular (Swagger, ReDoc)

## Port
- Container: 8000 (default)
- Docker compose: maps to 8000:8000

## Quick Start
```bash
python manage.py runserver 0.0.0.0:8000
python manage.py test
```

## Apps

### `products/`

#### Models
- **Category** — hierarchical categories with `parent` FK (self-referential). Bilingual: `name_uk`/`name_en`, `description_uk`/`description_en`. Active toggle.
- **Review** — one per (product, user); `author_name` is a first name only (never an email). `Product.rating`/`review_count` are recomputed from published reviews on every change and are read-only in the API (migration 0022 reset the hand-typed seed ratings to 0).
- **Product** — catalog item with bilingual fields, pricing in UAH, `warranty_months` (default 12, snapshotted by order-service per sale), images, `stock` (denormalized from inventory-service via event consumer). Locale-aware serialization via `Accept-Language` header.
- **Cart** — session-based (UUID `session_id` or `user_id` FK). No auth required for creation.
- **CartItem** — product + quantity per cart. Merge endpoint (`POST /api/cart/merge/`) for anonymous→user cart transfer after login.

#### Viewsets
- `CategoryViewSet` — CRUD, locale-aware list/detail
- `ProductViewSet` — CRUD, filtering (`DjangoFilterBackend`), search, ordering, locale-aware. `stock` is read-only: it follows inventory events and the hourly `reconcile_stock`.
- `CartViewSet` — list, add_item (`POST`), remove_item, merge. `authentication_classes = []` (anonymous writes).

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/products/` | Any | Product list (filtering incl. `?ids=1,2,3`, `?ordering=-rating`, pagination, locale-aware) |
| GET | `/api/products/{id}/reviews/` | Any | Published reviews (paginated) + `summary` {average, count, distribution}; signed in: `mine`, `can_review` |
| POST | `/api/products/{id}/reviews/` | Buyer | Create or replace own review `{rating 1-5, text}`; 403 unless order-service `purchased`, 503 if it is down |
| PATCH | `/api/reviews/{id}/` | Admin | `{is_published}`: hide or restore |
| GET | `/api/products/{id}/` | Any | Product detail (locale-aware) |
| POST | `/api/products/` | Admin | Create product |
| PUT | `/api/products/{id}/` | Admin | Update product |
| DELETE | `/api/products/{id}/` | Admin | Delete product |
| GET | `/api/products/{id}/similar/` | Any | Similar products (same category) |
| GET | `/api/categories/` | Any | Category list (locale-aware) |
| GET | `/api/categories/{id}/` | Any | Category detail (locale-aware) |
| POST | `/api/categories/` | Admin | Create category |
| GET | `/api/cart/` | Any | Cart contents (by session or user) |
| POST | `/api/cart/add_item/` | Any | Add item to cart |
| POST | `/api/cart/remove_item/` | Any | Remove item from cart |
| POST | `/api/cart/merge/` | Auth | Merge anonymous cart into user cart |
| GET | `/api/docs/` | Any | Swagger UI |
| GET | `/api/redoc/` | Any | ReDoc |

## Auth & Permissions
- `GatewayAuthentication` — base class for all Django services. Reads `X-Gateway-User-Id`, `X-Gateway-User-Role`, `X-Gateway-User-Email`, `X-Gateway-User-Name` from request headers. Creates/syncs local User record. Sets `is_staff = role == "admin"`, `is_superuser = role == "admin"`.
- `IsAdminUser` — admin-only writes.
- `IsAuthenticatedOrReadOnly` — authenticated for cart writes, any for catalog reads.
- Cart views: `authentication_classes = []` to allow anonymous session-based access (no CSRF on cart writes — tradeoff for UX).

## i18n / Bilingual Fields
- `name_uk`/`name_en`, `description_uk`/`description_en` on Product and Category.
- Serializer `to_representation()` reads `Accept-Language` header: `uk`/`uk-ua` → `_uk`, `en`/`en-*` → `_en`, else fallback to base field.
- Pricing: UAH default. USD when `Accept-Language: en` (rate 41.5).

## RabbitMQ Integration
- **Consumer (product-consumer container):** listens for `inventory.stock.changed` on `techhub.events` exchange, updates `Product.stock` and `Product.in_stock`.
- **Idempotency:** dedup via `ProcessedEvent` table (stores `event_id` + hash, skips duplicates).
- **Docker:** runs as separate `product-consumer` container in docker-compose.

## Tests
- **Framework:** Django `TestCase` + `APITestCase`
- **Coverage:** models (Category, Product), serializers, views (CRUD, filtering, pagination, bilingual), cart API, eventbus handler
- **Philosophy (AGENTS.md):** "Product-service tests are the spec. They represent desired behavior and must never be altered."
- **Run:** `python manage.py test`

## Known Issues
- Stock consumer is wired to `inventory.stock.changed` only: a GRN publishes it too, so also consuming `inventory.goods_received` counted receipts twice.
- Cart in product-service, orders in order-service: checkout spans two services (no distributed transaction).
