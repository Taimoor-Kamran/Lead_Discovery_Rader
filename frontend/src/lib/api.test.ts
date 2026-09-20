import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  getHealth,
  getMe,
  getReviewQueue,
  login,
  setSessionLostHandler,
  withQuery,
} from "./api";
import { clearAccessToken, getAccessToken, setAccessToken } from "./session";
import { envelope, routeFetch } from "@/test/utils";

const ME = { id: "1", email: "a@example.com", role: "admin", is_active: true };

beforeEach(() => clearAccessToken());
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("reads health", async () => {
    routeFetch({ "GET /health": { status: 200, body: { status: "ok", db: true, redis: true } } });
    await expect(getHealth()).resolves.toEqual({ status: "ok", db: true, redis: true });
  });

  it("sends credentials so the refresh cookie round-trips, and stores the token in memory", async () => {
    const { calls } = routeFetch({ "POST /auth/login": { status: 200, body: { access_token: "t" } } });
    await login("a@example.com", "a-password");
    expect(calls[0].init).toMatchObject({ credentials: "include" });
    expect(getAccessToken()).toBe("t");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.getItem("access_token")).toBeNull();
  });

  it("sends the access token as a bearer header", async () => {
    setAccessToken("the-token");
    const { calls } = routeFetch({ "GET /auth/me": { status: 200, body: ME } });
    await getMe();
    expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer the-token");
  });

  it("turns the error envelope into an ApiError", async () => {
    routeFetch({
      "POST /auth/login": {
        status: 401,
        body: envelope(401, "invalid_credentials", "Email or password is incorrect"),
      },
    });
    await expect(login("a@example.com", "nope")).rejects.toMatchObject({
      code: "invalid_credentials",
      requestId: "req-401",
      status: 401,
    });
    await expect(login("a@example.com", "nope")).rejects.toBeInstanceOf(ApiError);
  });

  it("refreshes once after a 401 and retries with the new token", async () => {
    setAccessToken("stale");
    let refreshed = false;
    const { calls } = routeFetch({
      "GET /auth/me": (init) => {
        const auth = (init?.headers as Record<string, string>).Authorization;
        return auth === "Bearer fresh"
          ? { status: 200, body: ME }
          : { status: 401, body: envelope(401, "unauthenticated", "expired") };
      },
      "POST /auth/refresh": () => {
        refreshed = true;
        return { status: 200, body: { access_token: "fresh" } };
      },
    });

    await expect(getMe()).resolves.toMatchObject({ email: "a@example.com" });

    expect(refreshed).toBe(true);
    expect(getAccessToken()).toBe("fresh");
    expect(calls.map((c) => `${c.method} ${c.url.replace(/^.*\/api\/v1/, "")}`)).toEqual([
      "GET /auth/me",
      "POST /auth/refresh",
      "GET /auth/me",
    ]);
    expect(calls[1].init).toMatchObject({ credentials: "include" });
  });

  it("gives up after one failed refresh and hands over to the session-lost handler", async () => {
    setAccessToken("stale");
    const lost = vi.fn();
    setSessionLostHandler(lost);
    const { calls } = routeFetch({
      "GET /review-queue": { status: 401, body: envelope(401, "unauthenticated", "expired") },
      "POST /auth/refresh": { status: 401, body: envelope(401, "unauthenticated", "no cookie") },
    });

    await expect(getReviewQueue()).rejects.toMatchObject({ status: 401 });

    expect(lost).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
    expect(calls.filter((c) => c.url.endsWith("/auth/refresh"))).toHaveLength(1);
    expect(calls).toHaveLength(2);
  });

  it("does not try to refresh a failed login", async () => {
    const { calls } = routeFetch({
      "POST /auth/login": { status: 401, body: envelope(401, "invalid_credentials", "nope") },
    });
    await expect(login("a@example.com", "nope")).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(1);
  });

  it("builds query strings and drops empty values", () => {
    expect(withQuery("/leads", { service: "seo_gbp", city: "", cursor: undefined, include_weak: false })).toBe(
      "/leads?service=seo_gbp&include_weak=false",
    );
    expect(withQuery("/leads")).toBe("/leads");
  });
});
