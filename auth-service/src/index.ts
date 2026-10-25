import express, { NextFunction, Request, Response } from "express";
import { metricsHandler, metricsMiddleware } from "./metrics";
import { toNodeHandler } from "better-auth/node";
import crypto from "crypto";
import { auth, pool } from "./auth";
import logger from "./logger";
import dotenv from "dotenv";
import { healthRoutes } from "./routes/healthRoutes";
import { internalRoutes } from "./routes/internalRoutes";
import { sessionRoutes } from "./routes/sessionRoutes";
import { adminRoutes } from "./routes/adminRoutes";
import { twoFactorRoutes } from "./routes/twoFactorRoutes";
import { usersRoutes } from "./routes/usersRoutes";
import { impersonateRoutes } from "./routes/impersonateRoutes";
import { openApiRouter } from "./openapi";
import {
  checkLoginRateLimit,
  recordFailedAttempt,
  resetLoginRateLimit,
  checkAndRecordSignUpAttempt,
  initRateLimiter,
  redisClient,
  adminRateLimit,
} from "./middleware/rateLimiter";
import { requireAdmin } from "./middleware/authMiddleware";
import { runMigrations } from "./db/migrations";
import { seedAdminUser, seedNonAdminUsers } from "./db/seeds";
import onFinished from "on-finished";

dotenv.config();

const requiredEnvVars = ["DATABASE_URL", "BETTER_AUTH_SECRET"] as const;
const optionalEnvVars = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "FRONTEND_URL"] as const;
const isTestEnv = process.env.VITEST === "true";

function validateEnv() {
  const missing = requiredEnvVars.filter((k) => !process.env[k] || process.env[k]!.trim() === "");
  if (missing.length > 0) {
    if (isTestEnv) { logger.warn("Test environment: missing required env vars", { missing }); }
    else { logger.error("FATAL: Missing required environment variables", { missing }); process.exit(1); }
  }
  const missingOptional = optionalEnvVars.filter((k) => !process.env[k] || process.env[k]!.trim() === "");
  if (missingOptional.length > 0) logger.warn("Optional environment variables not set", { missing: missingOptional });
}

validateEnv();

export function createApp() {
  const app = express();
  // req.ip = the rightmost X-Forwarded-For address outside private networks (Caddy, the nginx gateway):
  // without it every request came from the gateway and all users shared one login lockout counter
  app.set("trust proxy", "loopback, linklocal, uniquelocal");
  app.use(metricsMiddleware);
  app.get("/metrics", metricsHandler);

  const corsOrigins = (() => {
    const fe = process.env.FRONTEND_URL;
    if (fe) return [fe, fe.replace(/:\d+$/, "")];
    return ["http://localhost", "http://localhost:3000", "http://localhost:3001"];
  })();

  app.use(express.json());

  app.use((req: Request, res: Response, next: NextFunction) => {
    const requestId = (req.headers["x-request-id"] as string) || crypto.randomUUID();
    req.headers["x-request-id"] = requestId;
    res.setHeader("X-Request-Id", requestId);
    next();
  });

  app.use((req: Request, res: Response, next: NextFunction) => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(req.method)) {
      const origin = req.headers["origin"] as string | undefined;
      const referer = req.headers["referer"] as string | undefined;
      if (origin !== undefined || referer !== undefined) {
        const allowed = corsOrigins.some((o) => origin === o || referer?.startsWith(o + "/") || referer === o + "/");
        if (!allowed) return res.status(403).json({ success: false, message: "CSRF check failed" });
      }
    }
    next();
  });

  // Routes
  app.use(healthRoutes);
  app.use(openApiRouter);
  app.use(internalRoutes);

  // Sign-in rate limiter intercept (before Better Auth handler)
  app.use("/auth/sign-in/email", async (req: Request, res: Response, next: NextFunction) => {
    const ip = req.ip || req.socket.remoteAddress || "unknown";
    let allowed: boolean;
    let retryAfter: number | undefined;
    let redisDown = false;
    try {
      const result = await checkLoginRateLimit(ip);
      allowed = result.allowed;
      retryAfter = result.retryAfter;
    } catch (err) {
      logger.error("Rate limit check failed — blocking request as safety measure", { error: (err as Error).message, ip });
      allowed = false;
      retryAfter = 30;
      redisDown = true;
    }
    if (!allowed) {
      return res.status(429).json({
        success: false,
        message: redisDown
          ? "Service temporarily unavailable. Please try again shortly."
          : `Too many login attempts. Please try again in ${retryAfter} seconds.`,
        retryAfter,
      });
    }
    onFinished(res, () => {
      if (res.statusCode === 200) resetLoginRateLimit(ip).catch(() => {});
      else if (res.statusCode === 401 || res.statusCode === 403) recordFailedAttempt(ip).catch(() => {});
    });
    next();
  });

  // Sign-up rate limiter (atomic check+record BEFORE handler — no TOCTOU race)
  app.use("/auth/sign-up/email", async (req: Request, res: Response, next: NextFunction) => {
    const ip = req.ip || req.socket.remoteAddress || "unknown";
    let allowed: boolean;
    let retryAfter: number | undefined;
    let redisDown = false;
    try {
      const result = await checkAndRecordSignUpAttempt(ip);
      allowed = result.allowed;
      retryAfter = result.retryAfter;
    } catch (err) {
      logger.error("Sign-up rate limit check failed — blocking request as safety measure", { error: (err as Error).message, ip });
      allowed = false;
      retryAfter = 30;
      redisDown = true;
    }
    if (!allowed) {
      return res.status(429).json({
        success: false,
        message: redisDown
          ? "Service temporarily unavailable. Please try again shortly."
          : `Too many sign-up attempts. Please try again in ${retryAfter} seconds.`,
        retryAfter,
      });
    }
    // No onFinished needed — counter already incremented atomically
    next();
  });

  app.use("/auth/two-factor", twoFactorRoutes);
  app.use("/auth/admin", requireAdmin, adminRateLimit, usersRoutes);
  app.use("/auth", sessionRoutes);
  app.use("/auth", impersonateRoutes);
  app.use("/auth/admin", requireAdmin, adminRateLimit, adminRoutes);

  // Better Auth catch-all (must be last among /auth/*)
  app.all("/auth/*", (req: Request, res: Response, _next: NextFunction) => {
    const handler = toNodeHandler(auth);
    handler(req, res).catch((err: unknown) => {
      logger.error("Auth handler error", { error: (err as Error).message, path: req.path, method: req.method });
      if (!res.headersSent) res.status(500).json({ success: false, message: "Internal Server Error" });
    });
  });

  // Global error handler
  app.use((err: Error & { status?: number; statusCode?: number }, _req: Request, res: Response, _next: NextFunction) => {
    const statusCode = err.status || err.statusCode || 500;
    const message = statusCode < 500 ? err.message : "Internal Server Error";
    logger.error("Unhandled Express error", { error: err.message, stack: err.stack, statusCode });
    if (!res.headersSent) res.status(statusCode).json({ success: false, message });
  });

  app.all("/", async (_req: Request, res: Response) => { res.redirect(process.env.FRONTEND_URL || "http://localhost/"); });

  return app;
}

// --- Server startup (only in non-test mode) ---
if (!isTestEnv) {
  const PORT = process.env.PORT || 3001;
  const app = createApp();

  initRateLimiter()
    .then(() => logger.info("Rate limiter initialized (Redis)"))
    .catch((err) => logger.warn("Rate limiter unavailable, running without brute-force protection", { error: err.message }));

  let server: ReturnType<typeof app.listen>;

  runMigrations().finally(() => {
    seedAdminUser().finally(() => {
      seedNonAdminUsers();
      server = app.listen(PORT, () => {
        logger.info("Auth service running", { port: PORT });
        const providers = [process.env.GOOGLE_CLIENT_ID ? "Google" : null, process.env.GITHUB_CLIENT_ID ? "GitHub" : null].filter(Boolean);
        if (providers.length > 0) logger.info("OAuth providers configured", { providers });
      });
    });
  });

  function gracefulShutdown(signal: string) {
    logger.info(`Received ${signal}, shutting down gracefully...`);
    server.close(async () => {
      logger.info("HTTP server closed");
      try { await pool.end(); logger.info("Database pool closed"); } catch (err) { logger.error("Error closing database pool", { error: (err as Error).message }); }
      try { redisClient.quit(); logger.info("Redis connection closed"); } catch (err) { logger.error("Error closing Redis", { error: (err as Error).message }); }
      process.exit(0);
    });
    setTimeout(() => { logger.error("Forced shutdown after timeout"); process.exit(1); }, 30000).unref();
  }

  process.on("SIGTERM", () => gracefulShutdown("SIGTERM"));
  process.on("SIGINT", () => gracefulShutdown("SIGINT"));
}
