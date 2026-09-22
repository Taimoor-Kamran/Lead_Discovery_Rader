/**
 * Full-page screenshots of every screen at 1280 px, for the v0.9.0 before/after review.
 *
 *   node scripts/screenshots.mjs ../docs/design/before   (from frontend/)
 *
 * Needs the demo stack up with demo data (`make up && make load-demo-data`) and the
 * credentials from .env; `make screenshots` sources them. It signs in as the admin — the
 * one role that can open every page — and approves one opportunity through the API first
 * so that /leads and the lead detail page have something real to show. Nothing here talks
 * to an external service.
 */

import { mkdir } from "node:fs/promises";
import { chromium } from "@playwright/test";

const OUT = process.argv[2] ?? "docs/design/before";
const WEB = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const EMAIL = process.env.ADMIN_EMAIL;
const PASSWORD = process.env.ADMIN_PASSWORD;

if (!EMAIL || !PASSWORD) {
  console.error("ADMIN_EMAIL and ADMIN_PASSWORD must be set (they live in .env).");
  process.exit(2);
}

async function api(path, token, init = {}) {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path} → ${response.status} ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

/** One approved opportunity, so /leads and /leads/{id} are not empty in the screenshots. */
async function ensureLead(token) {
  const leads = await api("/leads?limit=1", token);
  if (leads.items.length) return leads.items[0].opportunity_id;
  const queue = await api("/review-queue?limit=1", token);
  const opportunity = queue.items[0]?.opportunities[0];
  if (!opportunity) throw new Error("the queue is empty: run `make load-demo-data` first");
  await api(`/opportunities/${opportunity.id}/review`, token, {
    method: "POST",
    body: JSON.stringify({
      decision: "approve",
      lock_version: opportunity.lock_version,
      note: "Screenshot fixture",
    }),
  });
  return opportunity.id;
}

const token = (await api("/auth/login", null, {
  method: "POST",
  body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
})).access_token;

const opportunityId = await ensureLead(token);
const businessId = (await api("/review-queue?limit=1", token)).items[0].business_id;
const searchJobId = (await api("/search-jobs?limit=5", token)).items[0].id;

const SHOTS = [
  ["01-login", "/login", false],
  ["02-review-queue", "/review", true],
  ["03-review-detail", `/review/${businessId}`, true],
  ["04-duplicates", "/duplicates", true],
  ["05-leads", "/leads", true],
  ["06-lead-detail", `/leads/${opportunityId}`, true],
  ["07-crm", "/crm", true],
  ["08-searches", "/searches", true],
  ["09-search-pipeline", `/searches/${searchJobId}`, true],
  ["10-users", "/admin/users", true],
  ["11-suppressions", "/admin/suppressions", true],
  ["12-health", "/admin/health", true],
  ["13-profile", "/profile", true],
];

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();

await page.goto(`${WEB}/login`);
await page.getByLabel("Email").fill(EMAIL);
await page.getByLabel("Password").fill(PASSWORD);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 30_000 });

for (const [name, path, signedIn] of SHOTS) {
  if (!signedIn) {
    // The sign-in page in its signed-out state: a fresh context without the session cookie.
    const anonymous = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const anonymousPage = await anonymous.newPage();
    await anonymousPage.goto(`${WEB}${path}`);
    await anonymousPage.waitForLoadState("networkidle");
    await anonymousPage.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
    await anonymous.close();
    console.log(`${OUT}/${name}.png`);
    continue;
  }
  await page.goto(`${WEB}${path}`);
  await page.waitForLoadState("networkidle");
  // Let the skeletons settle so a screenshot shows loaded content, never a loading frame.
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(`${OUT}/${name}.png`);
}

await browser.close();
