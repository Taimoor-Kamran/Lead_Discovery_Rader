import { describe, expect, it } from "vitest";
import { apiOriginOf, buildCsp, makeNonce, STATIC_SECURITY_HEADERS } from "./csp";

describe("content security policy", () => {
  it("locks scripts to the nonce and connections to the API origin", () => {
    const csp = buildCsp({ nonce: "abc123", apiOrigin: "http://127.0.0.1:8000" });
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).toContain("connect-src 'self' http://127.0.0.1:8000");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).not.toContain("ws:");
  });

  it("adds eval and websockets only in development", () => {
    const csp = buildCsp({ nonce: "n", apiOrigin: "http://localhost:8000", dev: true });
    expect(csp).toContain("'unsafe-eval'");
    expect(csp).toContain("ws:");
  });

  it("derives the API origin from the base URL", () => {
    expect(apiOriginOf("http://127.0.0.1:8000/api/v1")).toBe("http://127.0.0.1:8000");
    expect(apiOriginOf("not a url")).toBe("'self'");
  });

  it("makes a fresh base64 nonce each time", () => {
    const first = makeNonce();
    expect(first).toMatch(/^[A-Za-z0-9+/]+=*$/);
    expect(makeNonce()).not.toBe(first);
  });

  it("carries the same static headers as the API", () => {
    const keys = STATIC_SECURITY_HEADERS.map((header) => header.key);
    expect(keys).toEqual(["X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy"]);
  });
});
