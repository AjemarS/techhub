import { Pool, type QueryResult } from "pg";
import logger from "./logger";

async function queryWithRetry(pool: Pool, queryText: string, params?: any[], retries = 15, delay = 2000): Promise<QueryResult> {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      return await pool.query(queryText, params);
    } catch (error: any) {
      if (error.code === 'ECONNREFUSED' || error.message?.includes('getaddrinfo') || error.message?.includes('EAI_AGAIN')) {
        if (attempt === retries) {
          throw error;
        }
        console.warn(`Database query attempt ${attempt}/${retries} failed, retrying...`);
        await new Promise(resolve => setTimeout(resolve, delay));
      } else {
        throw error;
      }
    }
  }
  throw new Error("Unreachable");
}

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});
// pg emits "error" when the server drops an idle connection (DB restart); unhandled, it kills the process
pool.on("error", (err) => logger.error("PostgreSQL idle client error", { error: err.message }));

export interface AuditEntry {
  request_id?: string;
  event_id?: string;
  routing_key?: string;
  event_type: "rabbitmq" | "http_request";
  service: string;
  method?: string;
  path?: string;
  user_id?: string;
  status_code?: number;
  payload?: Record<string, unknown>;
  error_message?: string;
  duration_ms?: number;
}

export interface AuditQuery {
  page: number;
  limit: number;
  service?: string;
  event_type?: string;
  routing_key?: string;
  user_id?: string;
  request_id?: string;
  event_id?: string;
  from?: string;
  to?: string;
}

export async function initTables(): Promise<void> {
  await queryWithRetry(pool, `
    CREATE TABLE IF NOT EXISTS audit_logs (
      id BIGSERIAL PRIMARY KEY,
      request_id VARCHAR(255),
      event_id VARCHAR(255),
      routing_key VARCHAR(255),
      event_type VARCHAR(50) NOT NULL,
      service VARCHAR(100) NOT NULL,
      method VARCHAR(10),
      path TEXT,
      user_id VARCHAR(255),
      status_code INT,
      payload JSONB,
      error_message TEXT,
      duration_ms INT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_audit_event_id ON audit_logs(event_id);
    CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_logs(request_id);
    CREATE INDEX IF NOT EXISTS idx_audit_service ON audit_logs(service);
    CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_logs(event_type);
  `);
  logger.info("Audit tables initialized");
}

export async function insertEntry(entry: AuditEntry): Promise<void> {
  await queryWithRetry(pool,
    `INSERT INTO audit_logs (request_id, event_id, routing_key, event_type, service,
      method, path, user_id, status_code, payload, error_message, duration_ms)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)`,
    [
      entry.request_id || null,
      entry.event_id || null,
      entry.routing_key || null,
      entry.event_type,
      entry.service,
      entry.method || null,
      entry.path || null,
      entry.user_id || null,
      entry.status_code || null,
      entry.payload ? JSON.stringify(entry.payload) : null,
      entry.error_message || null,
      entry.duration_ms || null,
    ],
  );
}

export async function queryEntries(q: AuditQuery): Promise<{ rows: Record<string, unknown>[]; total: number }> {
  const clauses: string[] = [];
  const params: unknown[] = [];
  let idx = 0;

  if (q.service) { idx++; clauses.push(`service = $${idx}`); params.push(q.service); }
  if (q.event_type) { idx++; clauses.push(`event_type = $${idx}`); params.push(q.event_type); }
  if (q.routing_key) { idx++; clauses.push(`routing_key = $${idx}`); params.push(q.routing_key); }
  if (q.user_id) { idx++; clauses.push(`user_id = $${idx}`); params.push(q.user_id); }
  if (q.request_id) { idx++; clauses.push(`request_id = $${idx}`); params.push(q.request_id); }
  if (q.event_id) { idx++; clauses.push(`event_id = $${idx}`); params.push(q.event_id); }
  if (q.from) { idx++; clauses.push(`created_at >= $${idx}`); params.push(q.from); }
  if (q.to) { idx++; clauses.push(`created_at <= $${idx}`); params.push(q.to); }

  const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
  const offset = (q.page - 1) * q.limit;

  const totalResult = await queryWithRetry(pool, `SELECT COUNT(*) FROM audit_logs ${where}`, params);
  const total = parseInt(totalResult.rows[0].count, 10);

  const dataResult = await queryWithRetry(pool,
    `SELECT * FROM audit_logs ${where} ORDER BY created_at DESC LIMIT $${idx + 1} OFFSET $${idx + 2}`,
    [...params, q.limit, offset],
  );

  return { rows: dataResult.rows, total };
}
