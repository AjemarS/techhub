import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import request from "supertest";
import type { Express } from "express";

const mockCheckRateLimit = vi.hoisted(() => vi.fn());
const mockRecordFailed = vi.hoisted(() => vi.fn());
const mockResetRateLimit = vi.hoisted(() => vi.fn());
const mockGetSession = vi.hoisted(() => vi.fn());
const mockRevokeSession = vi.hoisted(() => vi.fn());
const mockListUserSessions = vi.hoisted(() => vi.fn());
const mockPool = vi.hoisted(() => ({ query: vi.fn(), end: vi.fn() }));

vi.mock("../auth", () => ({
  auth: { api: { getSession: mockGetSession, revokeSession: mockRevokeSession, listUserSessions: mockListUserSessions } },
  pool: mockPool,
}));
vi.mock("../middleware/rateLimiter", () => ({
  redisClient: { status: "ready", ping: vi.fn(), on: vi.fn(), quit: vi.fn() },
  checkLoginRateLimit: mockCheckRateLimit,
  recordFailedAttempt: mockRecordFailed,
  resetLoginRateLimit: mockResetRateLimit,
  initRateLimiter: vi.fn(),
  adminRateLimit: vi.fn((_: any, __: any, n: any) => n()),
}));
vi.mock("../logger", () => ({ default: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() } }));
vi.mock("../email/sender", () => ({
  sendVerificationEmail: vi.fn(), sendResetPasswordEmail: vi.fn(), sendImpersonationCode: vi.fn(),
}));
vi.mock("../middleware/authMiddleware", () => ({
  requireAuth: vi.fn((_: any, __: any, n: any) => n()),
  requireAdmin: vi.fn((_: any, __: any, n: any) => n()),
  getAdminUserIds: vi.fn(() => []), addAdminUserId: vi.fn(),
}));

import { createApp } from "../index.js";

let app: Express;

beforeAll(() => {
  app = createApp();
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("POST /auth/sign-in/email (rate limiter intercept)", () => {
  it("allows request when rate limit check passes", async () => {
    mockCheckRateLimit.mockResolvedValue({ allowed: true });
    mockGetSession.mockResolvedValue(null);

    const res = await request(app).post("/auth/sign-in/email").send({ email: "test@test.com", password: "password123" });
    expect(res.status).not.toBe(429);
    expect(mockCheckRateLimit).toHaveBeenCalled();
  });

  it("blocks request when rate limited", async () => {
    mockCheckRateLimit.mockResolvedValue({ allowed: false, retryAfter: 60 });

    const res = await request(app).post("/auth/sign-in/email").send({ email: "test@test.com", password: "password123" });
    expect(res.status).toBe(429);
    expect(res.body.message).toContain("60 seconds");
  });

  it("blocks with 30s retry when Redis is unavailable (fail-closed)", async () => {
    mockCheckRateLimit.mockRejectedValue(new Error("Redis down"));

    const res = await request(app).post("/auth/sign-in/email").send({ email: "test@test.com", password: "password123" });
    expect(res.status).toBe(429);
    expect(res.body.message).toContain("temporarily unavailable");
  });

  it("counts attempts per client IP behind the proxies, not per gateway", async () => {
    mockCheckRateLimit.mockResolvedValue({ allowed: true });
    mockGetSession.mockResolvedValue(null);

    // a client-supplied first entry is ignored; the gateway hop (private) is skipped
    await request(app).post("/auth/sign-in/email")
      .set("X-Forwarded-For", "198.51.100.7, 203.0.113.5, 172.18.0.1")
      .send({ email: "test@test.com", password: "password123" });
    expect(mockCheckRateLimit).toHaveBeenCalledWith("203.0.113.5");
  });
});

describe("POST /auth/sessions/revoke", () => {
  it("returns 401 when not authenticated", async () => {
    mockGetSession.mockResolvedValue(null);

    const res = await request(app).post("/auth/sessions/revoke");
    expect(res.status).toBe(401);
    expect(res.body.message).toBe("Not authenticated");
  });

  it("returns 500 on revoke error", async () => {
    mockGetSession.mockResolvedValue({ user: { id: "user-1" }, session: { token: "sess-1" } });
    mockRevokeSession.mockRejectedValue(new Error("Revoke failed"));

    const res = await request(app).post("/auth/sessions/revoke");
    expect(res.status).toBe(500);
  });
});

describe("POST /auth/admin/sessions/revoke", () => {
  it("revokes specific session by sessionToken", async () => {
    mockRevokeSession.mockResolvedValue({});

    const res = await request(app).post("/auth/admin/sessions/revoke").send({ sessionToken: "sess-1" });
    expect(res.status).toBe(200);
  });

  it("revokes all sessions by userId", async () => {
    mockRevokeSession.mockResolvedValue({});
    mockListUserSessions.mockResolvedValue({ sessions: [{ token: "s1" }, { token: "s2" }] });

    const res = await request(app).post("/auth/admin/sessions/revoke").send({ userId: "user-1" });
    expect(res.status).toBe(200);
    expect(mockRevokeSession).toHaveBeenCalledTimes(2);
  });

  it("returns 400 when neither sessionToken nor userId provided", async () => {
    const res = await request(app).post("/auth/admin/sessions/revoke").send({});
    expect(res.status).toBe(400);
  });
});
