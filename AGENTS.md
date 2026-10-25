# AGENTS.md

- Run full stack: `docker compose up` from root.
- Architecture: Gateway (Nginx + njs) → Auth (Express/Better Auth), Product (Django/DRF), Inventory (Django/DRF),
  Order (Django/DRF), Notification (Express), Audit (Express), Frontend (Next.js 16); Prometheus + Grafana scrape
  every service's `/metrics`.
  See per-service CONTEXT.md for specifics.
- Auth flow: Nginx proxies auth subrequest to /auth/me (auth-service returns X-User-* response headers) →
  njs checkAndProxy evaluates role-based permissions → injects X-Gateway-User-* headers →
  downstream Django services trust them via GatewayAuthentication.
  Frontend protects routes via proxy.ts middleware.
- Role-based access (njs + service permissions): anonymous=catalog+cart+placing/paying an order, user=own orders,
  returns, reviews, cashier=POS, warehouse_worker=stock+GRN+purchase orders, admin=full. Matrix: CONCEPT.md §2.3.
- Event bus: RabbitMQ topic exchange `techhub.events`. Publishers: order-service, inventory-service.
  Consumers: product-consumer (inventory.stock.changed), notification-service (order events,
  low stock), audit-service (all events, `#`).
- Route ordering matters in nginx: prefix matches `/api/{path}`, `/media/{path}`, `/auth/{path}` must be declared
  before `/{path}`. nginx matches longest prefix first.
- Nginx config syntax check: `docker compose exec gateway nginx -t` (hostnames resolve only inside the compose network).
- Services reference: `product-service/CONTEXT.md`, `auth-service/CONTEXT.md`, `nginx/CONTEXT.md`,
  `frontend/CONTEXT.md`, `inventory-service/CONTEXT.md`, `order-service/CONTEXT.md`.
- Service test commands:
  - product-service: `docker compose exec product-service python manage.py test`
  - inventory-service: `docker compose exec inventory-service python manage.py test`
  - order-service: `docker compose exec order-service python manage.py test`
  - auth-service: `docker compose exec auth-service npm run test`
  - frontend: from `frontend/` on the host, `PLAYWRIGHT_BASE_URL=http://localhost npx playwright test` (the image has no
    browsers; visual baselines are Linux-only, regenerate them in `mcr.microsoft.com/playwright:v1.61.0-noble`
    with its own dev server and no backend, as CI runs them: no `PLAYWRIGHT_BASE_URL`, no `--network host`)

