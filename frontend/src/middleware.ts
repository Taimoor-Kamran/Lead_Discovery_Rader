import { NextResponse, type NextRequest } from "next/server";
import { apiOriginOf, buildCsp, makeNonce, STATIC_SECURITY_HEADERS } from "@/lib/csp";

/**
 * Per-request CSP with a nonce (spec v0.8.0 §5). Next.js picks the nonce up from the
 * `Content-Security-Policy` request header and puts it on its own inline scripts, and the
 * root layout reads `headers()` so every page renders per request rather than at build time
 * (a build-time page could not carry a per-request nonce).
 *
 * `Strict-Transport-Security` is deliberately absent: this spec serves plain http on
 * 127.0.0.1; the reverse proxy of the future VPS spec adds it where TLS terminates.
 */
export function middleware(request: NextRequest) {
  const nonce = makeNonce();
  const csp = buildCsp({
    nonce,
    apiOrigin: apiOriginOf(process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"),
    dev: process.env.NODE_ENV === "development",
  });

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  for (const { key, value } of STATIC_SECURITY_HEADERS) response.headers.set(key, value);
  return response;
}

export const config = {
  matcher: [
    {
      // Everything but Next's static assets and the favicon; prefetches are skipped so
      // a prefetched page never carries a nonce that differs from the one it renders with.
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
