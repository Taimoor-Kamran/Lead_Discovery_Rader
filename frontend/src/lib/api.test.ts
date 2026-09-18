import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getHealth, getMe, login } from "./api";

function mockFetch(status: number, body: unknown) {
  const spy = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("reads health", async () => {
    mockFetch(200, { status: "ok", db: true, redis: true });
    await expect(getHealth()).resolves.toEqual({ status: "ok", db: true, redis: true });
  });

  it("sends credentials so the refresh cookie round-trips", async () => {
    const spy = mockFetch(200, { access_token: "t" });
    await login("a@example.com", "a-password");
    expect(spy.mock.calls[0][1]).toMatchObject({ credentials: "include" });
  });

  it("sends the access token as a bearer header", async () => {
    const spy = mockFetch(200, { id: "1", email: "a@example.com", role: "admin" });
    await getMe("the-token");
    const init = spy.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer the-token");
  });

  it("turns the error envelope into an ApiError", async () => {
    mockFetch(401, {
      error: {
        code: "invalid_credentials",
        message: "Email or password is incorrect",
        request_id: "req-1",
        details: {},
      },
    });

    await expect(login("a@example.com", "nope")).rejects.toMatchObject({
      code: "invalid_credentials",
      requestId: "req-1",
      status: 401,
    });
    await expect(login("a@example.com", "nope")).rejects.toBeInstanceOf(ApiError);
  });
});
