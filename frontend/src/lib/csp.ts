/**
 * The web app's Content-Security-Policy (spec v0.8.0 §5). Documented policy:
 *
 * - `default-src 'self'`: nothing loads from anywhere but this origin;
 * - `script-src 'self' 'nonce-…' 'strict-dynamic'`: Next.js's own inline bootstrap scripts
 *   carry the per-request nonce the middleware generates (Next reads it from the CSP
 *   header); no other inline script runs. Development adds `'unsafe-eval'` for React Fast
 *   Refresh only;
 * - `style-src 'self' 'unsafe-inline'`: Tailwind ships as a stylesheet, but Next.js and
 *   React set a few inline style attributes (the loading indicator, error overlay), which
 *   a style nonce does not cover;
 * - `connect-src 'self' <API origin>`: the only place `fetch` may talk to; development adds
 *   `ws:` for hot reload;
 * - `img-src 'self' data: blob:`, `font-src 'self' data:`, `object-src 'none'`,
 *   `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'` (the header form of
 *   `X-Frame-Options: DENY`).
 */

export type CspOptions = { nonce: string; apiOrigin: string; dev?: boolean };

export function apiOriginOf(apiBaseUrl: string): string {
  try {
    return new URL(apiBaseUrl).origin;
  } catch {
    return "'self'";
  }
}

export function buildCsp({ nonce, apiOrigin, dev = false }: CspOptions): string {
  const script = ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'"];
  if (dev) script.push("'unsafe-eval'");
  const connect = ["'self'", apiOrigin];
  if (dev) connect.push("ws:", "wss:");
  return [
    "default-src 'self'",
    `script-src ${script.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connect.join(" ")}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** The headers every web response carries besides the CSP (also set in next.config.mjs). */
export const STATIC_SECURITY_HEADERS: readonly { key: string; value: string }[] = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

/** A fresh nonce per request: 16 random bytes, base64. */
export function makeNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
