# Audit Service

Centralized audit logging for TechHub — captures HTTP request metadata and RabbitMQ events from all services into a single queryable store.

---

## Stack

- **Runtime:** Node.js 24 LTS (Alpine in Docker)
- **Language:** TypeScript (compiled via `tsc`, dev via `tsx watch`)
- **HTTP:** Express 4.21
- **Database:** PostgreSQL 15 (`logging_db`, shared with notification-service)
- **Message Broker:** RabbitMQ via `amqplib` 0.10
- **Logging:** Winston 3.19 (JSON format)

---

## Port

- Container: 8005 (default, configurable via `PORT`)
- Docker compose maps to 8005:8005

---

## Quick Start

```bash
npm run dev       # tsx watch src/index.ts (hot reload)
npm run build     # tsc
npm run start     # node dist/index.js
```

---

## API Endpoints

All routes are prefixed with `/api/audit/`:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/audit/log` | Internal | Ingest HTTP audit event from services |
| GET | `/api/audit/logs` | Admin | Query audit logs (paginated, filterable) |
| GET | `/api/audit/health` | None | Health check (DB + consumer status) |

### POST `/api/audit/log`

Request body:

```json
{
  "request_id": "uuid",
  "event_id": "uuid",
  "event_type": "http_request",
  "service": "product-service",
  "method": "GET",
  "path": "/api/products/",
  "user_id": "user-uuid",
  "status_code": 200,
  "payload": {},
  "error_message": null,
  "duration_ms": 42
}
```

### GET `/api/audit/logs`

Query parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `page` | int | Page number (default: 1) |
| `limit` | int | Results per page (1–100, default: 50) |
| `service` | string | Filter by service name |
| `event_type` | string | Filter: `http_request` or `rabbitmq` |
| `routing_key` | string | Filter by event key, e.g. `order.created` |
| `user_id` | string | Filter by user UUID |
| `request_id` | string | Filter by request UUID |
| `event_id` | string | Filter by event UUID |
| `from` | ISO datetime | Filter entries after timestamp |
| `to` | ISO datetime | Filter entries before timestamp |

Response:

```json
{
  "success": true,
  "results": [...],
  "total": 1500,
  "page": 1,
  "limit": 50
}
```

---

## Event Consumption

**Exchange:** `techhub.events` (topic, durable)
**Queue:** `audit.all` (bound with `#` — captures all events)

| Binding Key | Action |
|-------------|--------|
| `#` | All events persisted to `audit_logs` table |

Every message on the exchange is captured with its routing key, event payload, and metadata. No dedup — every event is logged as received.

---

## Database (`logging_db`)

### `audit_logs` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL | Primary key |
| `request_id` | VARCHAR(255) | HTTP request UUID |
| `event_id` | VARCHAR(255) | RabbitMQ event UUID |
| `routing_key` | VARCHAR(255) | RabbitMQ routing key |
| `event_type` | VARCHAR(50) | `http_request` or `rabbitmq` |
| `service` | VARCHAR(100) | Source service name |
| `method` | VARCHAR(10) | HTTP method (HTTP events) |
| `path` | TEXT | Request path (HTTP events) |
| `user_id` | VARCHAR(255) | Authenticated user UUID |
| `status_code` | INT | HTTP status code |
| `payload` | JSONB | Event payload or HTTP body |
| `error_message` | TEXT | Error details (if any) |
| `duration_ms` | INT | Request duration (HTTP events) |
| `created_at` | TIMESTAMPTZ | Auto-set to NOW() |

Indexes on: `created_at DESC`, `event_id`, `request_id`, `service`, `event_type`.

---

## Auth

No custom auth middleware. The audit service is:

- **POST `/api/audit/log`** — called internally by Django services via `AuditMiddleware` (fire-and-forget from `shared-lib`). Not exposed through the gateway.
- **GET `/api/audit/logs`** — intended for admin queries. No auth enforcement in service — relies on gateway RBAC to restrict access.
- **GET `/api/audit/health`** — unauthenticated health probe.

---

## Source Structure

```
src/
├── index.ts        # Express app setup, tables init, consumer start
├── routes.ts       # POST /log, GET /logs, GET /health
├── store.ts        # PostgreSQL pool, CRUD operations, table creation
├── consumer.ts     # RabbitMQ consumer (binds to techhub.events with #)
└── logger.ts       # Winston logger (JSON format, service: audit-service)
```

---

## Architecture Notes

- **Dual capture:** Both HTTP request metadata (via `AuditMiddleware` in Django services) and RabbitMQ events (via catch-all consumer) are captured.
- **Consumer startup non-fatal:** If RabbitMQ is unavailable, the service starts without the consumer and logs a warning. The health endpoint reports consumer status.
- **Startup retry:** Database connection retries up to 15 times (2s intervals) for resilience during compose startup.
- **Shared database:** Uses `logging_db` alongside notification-service. No cross-table coupling — each service owns its tables.
- **No migration tool:** Schema bootstrapped via `CREATE TABLE IF NOT EXISTS` on startup.
