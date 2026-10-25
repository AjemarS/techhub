import { Router, Request, Response } from "express";
import logger from "./logger";
import { insertEntry, queryEntries } from "./store";
import { isConsumerConnected } from "./consumer";

const router = Router();

// POST /api/audit/log — ingest HTTP audit events from services
router.post("/api/audit/log", async (req: Request, res: Response) => {
  try {
    const body = req.body;
    await insertEntry({
      request_id: body.request_id,
      event_id: body.event_id,
      event_type: body.event_type || "http_request",
      service: body.service || "unknown",
      method: body.method,
      path: body.path,
      user_id: body.user_id,
      status_code: body.status_code,
      payload: body.payload,
      error_message: body.error_message,
      duration_ms: body.duration_ms,
    });
    res.json({ success: true });
  } catch (err) {
    logger.error("Failed to insert audit entry", { error: (err as Error).message });
    res.status(500).json({ success: false, message: "Failed to log" });
  }
});

// GET /api/audit/logs — admin query
router.get("/api/audit/logs", async (req: Request, res: Response) => {
  // Gateway sets this header after auth; requests that bypass the gateway get nothing
  if (req.header("x-gateway-user-role") !== "admin") {
    res.status(403).json({ success: false, message: "Admin only" });
    return;
  }
  try {
    const page = Math.max(1, parseInt(req.query.page as string) || 1);
    const limit = Math.min(100, Math.max(1, parseInt(req.query.limit as string) || 50));

    const result = await queryEntries({
      page,
      limit,
      service: req.query.service as string | undefined,
      event_type: req.query.event_type as string | undefined,
      routing_key: req.query.routing_key as string | undefined,
      user_id: req.query.user_id as string | undefined,
      request_id: req.query.request_id as string | undefined,
      event_id: req.query.event_id as string | undefined,
      from: req.query.from as string | undefined,
      to: req.query.to as string | undefined,
    });

    res.json({
      success: true,
      results: result.rows,
      total: result.total,
      page,
      limit,
    });
  } catch (err) {
    logger.error("Failed to query audit logs", { error: (err as Error).message });
    res.status(500).json({ success: false, message: "Query failed" });
  }
});

// GET /api/audit/health
router.get("/api/audit/health", (_req: Request, res: Response) => {
  res.json({
    status: "healthy",
    service: "audit-service",
    consumer: isConsumerConnected() ? "connected" : "disconnected",
  });
});

export default router;
