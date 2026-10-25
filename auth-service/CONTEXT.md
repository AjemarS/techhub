# Auth Service

## Stack
- **Runtime:** Node.js 24 LTS (Alpine in Docker)
- **Framework:** Express + TypeScript (tsx watch for dev, tsc for build)
- **Auth Library:** Better Auth v1.4.3
- **Database:** PostgreSQL 15 (`auth_db`, port 5433)
- **Cache:** Redis 7 (rate limiting via ioredis + Lua scripting, session cache)
- **Logging:** Winston (colorized dev, JSON/production + file rotation)
- **Validation:** Zod v4 (request body + query param validation)
- **API Docs:** OpenAPI 3.0 + swagger-ui-express
- **Testing:** Vitest + Supertest (119 tests, 10 files)

## Port
- Container: 3001 (default, configurable via `PORT`)
- Docker compose maps to 3001:3001

## Quick Start
```bash
npm run dev         # tsx watch src/index.ts (hot reload)
npm run build       # tsc
npm run start       # node dist/index.js
npm run typecheck   # tsc --noEmit
npm run test        # vitest run
npm run test:watch  # vitest watch mode
npm run migrate     # auth migrate --yes (DB schema via Better Auth CLI)
```

## Architecture

### Directory Structure
```
src/
├── index.ts                       # App factory + server startup (140 lines)
├── auth.ts                        # Better Auth instance + DB pool
├── openapi.ts                     # OpenAPI 3.0 spec + swagger-ui router
├── logger.ts                      # Winston logger (dev/prod)
├── middleware/
│   ├── authMiddleware.ts          # requireAuth, requireAdmin
│   ├── rateLimiter.ts             # Login brute-force + admin rate limit (Lua scripts)
│   ├── sessionCache.ts            # Redis read-through session cache
│   ├── auditLog.ts                # Structured audit log writer (retry on failure)
│   └── validate.ts                # Zod validation middleware factory
├── routes/
│   ├── healthRoutes.ts            # GET /health
│   ├── sessionRoutes.ts           # GET /auth/me, GET /auth/sessions, POST /auth/sessions/revoke
│   ├── adminRoutes.ts             # GET/POST /auth/admin/audit-logs, POST /auth/admin/sessions/revoke
│   ├── twoFactorRoutes.ts         # POST /auth/two-factor/enable, /disable
│   ├── usersRoutes.ts             # Admin user CRUD + set-role
│   └── impersonateRoutes.ts       # Admin impersonate, cashier code-based impersonation
├── validation/
│   ├── schemas.ts                 # Zod schemas for request bodies
│   └── querySchemas.ts            # Zod schemas for query params (pagination, filters)
├── db/
│   ├── migrations.ts              # runMigrations (audit_log table)
│   └── seeds.ts                   # seedAdminUser, seedNonAdminUsers
├── helpers/
│   └── betterAuth.ts             # Typed wrappers for Better Auth API responses
├── email/
│   └── sender.ts                  # Resend transactional email
└── __tests__/
    ├── helpers/                   # (empty — mocks inline per file)
    ├── schemas.test.ts            # 32 tests — Zod schema validation
    ├── authMiddleware.test.ts     # 8 tests — requireAuth, requireAdmin
    ├── rateLimiter.test.ts        # 12 tests — login + admin rate limiter, Lua eval + fallback
    ├── email.test.ts              # 2 tests — Resend-not-configured path
    ├── health.integration.test.ts # 5 tests — DB/Redis state combos
    ├── twoFactor.integration.test.ts  # 8 tests — enable/disable, auth, validation
    ├── users.integration.test.ts  # 22 tests — CRUD, set-role, sessions, audit log
    ├── impersonation.integration.test.ts # 13 tests — admin + cashier impersonation
    ├── me.integration.test.ts     # 9 tests — /auth/me, /auth/sessions, CORS
    └── signin.integration.test.ts # 8 tests — rate limiter, session revoke
```

**Note:** Routes call Better Auth API directly (no controller/service layer).

### Key Routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Deep health check (DB + Redis probes) |
| GET | `/auth/me` | Session | Returns session/user + X-User-* headers (used by gateway auth_request) |
| GET | `/auth/sessions` | Session | List own active sessions |
| POST | `/auth/sessions/revoke` | Session | Revoke own session |
| POST | `/auth/sign-in/email` | None | Sign-in (intercepted by Redis-backed rate limiter) |
| POST | `/auth/two-factor/enable` | Session + password | Enable TOTP 2FA |
| POST | `/auth/two-factor/disable` | Session + password | Disable TOTP 2FA |
| GET | `/auth/admin/users` | Admin | List users |
| GET | `/auth/admin/users/:id` | Admin | Get user by ID |
| POST | `/auth/admin/users` | Admin | Create user |
| PUT | `/auth/admin/users/:id` | Admin | Update user |
| DELETE | `/auth/admin/users/:id` | Admin | Delete user |
| GET | `/auth/admin/users/:id/sessions` | Admin | List user sessions |
| POST | `/auth/admin/set-role` | Admin | Set user role |
| POST | `/auth/admin/sessions/revoke` | Admin | Revoke any session (by token or all for user) |
| GET | `/auth/admin/audit-logs` | Admin | List audit logs (paginated, filterable) |
| POST | `/auth/admin/impersonate` | Admin | Impersonate user by ID |
| POST | `/auth/admin/stop-impersonation` | Session | Restore original session |
| POST | `/auth/impersonate/request-code` | Session | Cashier: request 6-digit code to user email |
| POST | `/auth/impersonate/verify-code` | Session | Cashier: verify code and start impersonation |
| GET | `/api/internal/users` (`?role=`), `/api/internal/users/:id`, `/api/internal/users/email/:email`; POST `/api/internal/users/batch` | none — compose network only | Name/email lookups for other services. The gateway does not route them (404); in dev compose port 3001 is published, so they are reachable from the LAN |
| GET | `/api-docs` | None | Swagger UI |
| GET | `/api-docs.json` | None | Raw OpenAPI spec |
| ALL | `/auth/*` | Session | Catch-all: Better Auth built-in handlers (sign-up, OAuth callbacks, forgot-password, etc.) |

### Role Schema
```typescript
role: z.enum(["admin", "user", "cashier", "warehouse_worker"])
```

### Default Error Response Format
```json
{ "success": false, "message": "Error description", "errors": { "field": ["error"] } }
```
All endpoints use this consistent shape. Rate limit responses also include `retryAfter`.

### Route Mount Order (index.ts)
1. Global middleware: CORS → JSON parse → request tracing → CSRF
2. `healthRoutes` — `/health`
3. `openApiRouter` — `/api-docs`, `/api-docs.json`
4. Rate limiter intercept — `/auth/sign-in/email`
5. `twoFactorRoutes` — `/auth/two-factor`
6. `usersRoutes` — `/auth/admin` (requireAdmin + adminRateLimit)
7. `sessionRoutes` — `/auth` (me, sessions, revoke)
8. `impersonateRoutes` — `/auth` (impersonation)
9. `adminRoutes` — `/auth/admin` (requireAdmin + adminRateLimit)
10. Better Auth catch-all — `/auth/*` (most be last)
11. Global error handler
12. `/` → redirect to frontend

### Gateway Integration (Auth Headers)
`/auth/me` returns response headers alongside JSON body:
- `X-User-Id` — user UUID
- `X-User-Role` — role string
- `X-User-Email` — email
- `X-User-Name` — display name

Gateway injects these as `X-Gateway-User-*` headers to downstream Django services.

## Authentication & Authorization

### Session Verification (Gateway Integration)
1. Gateway makes internal `auth_request` to `/auth/me` with user cookies
2. Auth service returns session user (JSON) + `X-User-*` response headers
3. njs `checkAndProxy` evaluates role-based permissions
4. Gateway injects `X-Gateway-User-*` headers to downstream services
5. Django `GatewayAuthentication` trusts these headers (internal network only)

### RBAC Source of Truth
`user.role` DB column is the single source. `requireAdmin` middleware checks `session.user.role` from DB. Gateway njs also reads role from `/auth/me` `X-User-Role` header. No env-var whitelist — the in-memory `ADMIN_USER_IDS` list has been removed.

### Brute-Force Protection
- **Redis-backed** with exponential backoff via atomic Lua script:
  - 5 failed attempts → 1 minute block
  - 10 → 5 minutes
  - 20 → 30 minutes
  - 50+ → 1 hour
- **Fail-closed:** if Redis is unavailable, sign-ins are blocked with 30s backoff
- 3-layer fallback: EVALSHA → EVAL → non-atomic INCR/EXPIRE/SETEX

### Admin Rate Limiting
- Separate window for admin API endpoints
- Keyed by `userId + IP`
- Limits: 30 req/min for GET, 10 req/min for mutations
- **Fail-open:** allows request if Redis is down
- Covers: user CRUD, set-role, session revocation, audit log write

### Session Cache
- Redis read-through cache for `getSession` calls (hot path)
- TTL: 24 hours (matches session refresh window)
- Keyed by session token from `better-auth.session_token` cookie
- Invalidates on session revoke (own + admin)
- Graceful degradation on cache miss / Redis down

### Session Configuration
- Expiry: 7 days (`expiresIn`)
- Refresh window: 24 hours (`updateAge`)
- Cookie prefix: `better-auth`
- Host-only cookies (no `Domain`): a parent-domain cookie from prod would shadow staging's session

## Security Features
- [x] Brute-force protection (Redis, Lua atomic, exponential backoff, fail-closed)
- [x] Admin rate limiting (30 GET / 10 mutation req/min per user+IP)
- [x] Admin authorization (DB `user.role`, single source)
- [x] Input validation (Zod schemas on all endpoints + query params)
- [x] Password policy (min 8, uppercase + lowercase + digit, common-password blocklist)
- [x] 2FA (TOTP enable/disable with password verification)
- [x] Session cache (Redis, reduces DB load on hot path)
- [x] CSRF protection (Origin/Referer validation against CORS whitelist)
- [x] Request tracing (X-Request-Id generation and propagation)
- [x] Graceful shutdown (SIGTERM/SIGINT → drain → close pool/redis → exit)
- [x] Deep health check (DB + Redis probes)
- [x] Structured audit log (DB-backed, retry on failure)
- [x] CORS from config (FRONTEND_URL + localhost fallbacks)
- [ ] Email verification (disabled, can enable by setting RESEND_API_KEY)

## Database
- PostgreSQL schema managed by Better Auth CLI
- Tables: `user`, `session`, `account`, `verification` (Better Auth) + `audit_log` (custom)
- Custom `status` field on `user` table (default: `"active"`)
- Migration files in `better-auth_migrations/` (auto-generated by CLI)
- `audit_log` table created by `runMigrations()` at startup

### Migrations
```bash
npm run migrate   # auth migrate --yes (Better Auth tables)
```
Custom tables (audit_log) auto-created on server start.

## OAuth Providers
- Google (requires `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`)
- GitHub (requires `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`)

## Admin Bootstrapping
On first startup (when no admin exists in DB):
1. Check if any `user.role = 'admin'` exists → skip if true
2. Look up `ADMIN_EMAIL` in DB — if user exists, promote to admin
3. If not: create user + credential account, seed as admin
4. Dev users: cashier@techhub.local, warehouse@techhub.local, customer@techhub.local (password: `password123`)

## Tests
- **Status:** 119 tests across 10 files, all passing
- **Framework:** Vitest (unit) + Supertest (integration)
- **Coverage:** Schema validation, auth middleware, rate limiter (Lua + fallback), email sender, all custom routes
- **Run:** `npm run test`
- **Coverage config:** V8 provider, thresholds: statements 50%, branches 40%, functions 50%, lines 50%
