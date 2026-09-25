/**
 * The accessibility pass v0.9.0 asks for: axe over the main screens, zero serious or
 * critical violations. Each screen is rendered the way a page renders it — inside the
 * AppShell, so the landmarks, the skip link and the heading order are all under test.
 *
 * Colour contrast is audited by the numbers in src/lib/contrast.test.ts instead: jsdom
 * computes no colours, so axe cannot judge it here.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { AppShell } from "@/components/AppShell";
import { HealthPage } from "@/components/admin/HealthPage";
import { CrmDashboard } from "@/components/crm/CrmDashboard";
import { LeadDetail } from "@/components/leads/LeadDetail";
import { Leads } from "@/components/leads/Leads";
import { ReviewQueue } from "@/components/review/ReviewQueue";
import { BusinessReview } from "@/components/review/BusinessReview";
import { Searches } from "@/components/searches/Searches";
import { SignInPanel } from "@/components/SignInPanel";
import { axeViolations } from "@/test/axe";
import {
  leadDetail,
  leadRead,
  me,
  queueItem,
  renderWithProviders,
  reviewDetail,
  routeFetch,
  type RouteHandler,
} from "@/test/utils";

afterEach(() => vi.unstubAllGlobals());

const HEALTH = {
  generated_at: "2026-09-20T10:00:00Z",
  environment: "local",
  db: true,
  redis: true,
  jobs: { last_24h: [], last_7d: [] },
  sources: [],
  timings: [],
  queue: { name: "default", length: 0, scheduler_lock_held: true, schedule: [] },
  ai: {
    provider: "fake",
    calls_today: 0,
    call_cap: 500,
    classifications_today: 0,
    reused_today: 0,
    reuse_rate: null,
    spent_today_usd: 0,
    budget_usd: 2,
    budget_ratio: 0,
    prices_configured: false,
  },
  data_quality: {
    records_total: 0,
    records_invalid: 0,
    invalid_rate: null,
    businesses_total: 0,
    missing_city: 0,
    missing_phone: 0,
    missing_website: 0,
  },
  duplicates: { auto_merged: 0, sent_to_review: 0, merged_by_review: 0, kept_apart: 0, pending_review: 0 },
  crm: { destination: "fake", scheduled: 0, held: 0, synced_today: 0 },
  freshness: [],
  audits: { done: 0, robots_blocked: 0, unreachable: 0, failed: 0, skipped: 0, total: 0 },
  backups: {
    directory: "/app/backups",
    backups_kept: 0,
    keep: 14,
    last_backup_file: null,
    last_backup_at: null,
    last_backup_size_bytes: null,
    last_verify_at: null,
    last_verify_ok: null,
    last_verify_file: null,
    last_verify_error: null,
  },
  thresholds: {
    job_success_rate_min: 0.8,
    source_error_rate_max: 0.2,
    ai_budget_ratio: 0.8,
    backup_max_age_hours: 36,
    queue_length_max: 500,
    watchdog_stale_minutes: 30,
  },
  alerts: [
    {
      id: "al-1",
      rule: "crm_held",
      severity: "warning",
      message: "2 CRM lead(s) are held",
      details: {},
      first_seen_at: "2026-09-20T09:00:00Z",
      last_seen_at: "2026-09-20T10:00:00Z",
      acknowledged_at: null,
      acknowledged_by: null,
      cleared_at: null,
      active: true,
      acknowledged: false,
    },
  ],
};

const CRM_STATUS = {
  destination: "fake",
  demo: true,
  auto_sync: true,
  sync_delay_minutes: 30,
  health: { destination: "fake", ok: true, checks: [{ name: "store", ok: true, detail: "2 records" }], message: null },
  counts: { scheduled: 1, syncing: 0, synced: 2, held: 0, cancelled: 0, withdrawn: 0 },
};

const SEARCH_JOB = {
  id: "job-1",
  name: "Austin plumbers",
  industry: "plumber",
  geo: { city: "Austin", state: "TX" },
  status: "active",
  max_results: 20,
  created_at: "2026-09-20T09:00:00Z",
  updated_at: "2026-09-20T09:00:00Z",
  last_run: { id: "run-1", status: "done", created_at: "2026-09-20T09:00:00Z", finished_at: "2026-09-20T09:05:00Z" },
};

const ESTIMATE = {
  max_results: 20,
  uses_places: true,
  places_max_calls: 4,
  places_daily_cap: 200,
  places_remaining_today: 199,
  pagespeed_calls: 20,
  pagespeed_daily_cap: 200,
  pagespeed_remaining_today: 200,
  ai_enabled: false,
  ai_provider: "fake",
  ai_calls: 0,
  ai_budget_usd: 2,
  ai_budget_remaining_usd: 2,
  can_run: true,
  blockers: [],
};

/** Render one screen inside the shell, wait for it to settle, then run axe over the page. */
async function screenIsClean(
  ui: React.ReactElement,
  routes: Record<string, RouteHandler>,
  settled: () => unknown,
  role: Parameters<typeof me>[0] = "admin",
) {
  routeFetch(routes);
  const { container } = renderWithProviders(<AppShell>{ui}</AppShell>, { user: me(role) });
  await waitFor(settled);
  expect(await axeViolations(container)).toEqual([]);
}

describe("the axe harness itself", () => {
  it("catches a real serious violation, so a green run means something", async () => {
    const { container } = renderWithProviders(
      <main>
        {/* No accessible name: axe's "button-name" rule, impact "critical". */}
        <button type="button" />
      </main>,
    );
    const found = await axeViolations(container);
    expect(found.map((violation) => violation.id)).toContain("button-name");
  });

  it("does not report a rule that jsdom cannot judge", async () => {
    const { container } = renderWithProviders(
      <main>
        <p style={{ color: "#fff", background: "#fff" }}>invisible</p>
      </main>,
    );
    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("axe: no serious or critical violations", () => {
  it("sign in", async () => {
    routeFetch({ "GET /health": { status: 200, body: { status: "ok", db: true, redis: true } } });
    const { container } = renderWithProviders(
      <main>
        <h1>Lead Discovery Radar</h1>
        <SignInPanel />
      </main>,
      { user: null, status: "anonymous" },
    );
    await waitFor(() => expect(screen.getByTestId("health")).toBeTruthy());
    expect(await axeViolations(container)).toEqual([]);
  });

  it("review queue", async () => {
    await screenIsClean(
      <ReviewQueue />,
      { "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } } },
      () => expect(screen.getAllByTestId("queue-row")).toHaveLength(1),
      "reviewer",
    );
  });

  it("review queue, empty", async () => {
    await screenIsClean(
      <ReviewQueue />,
      { "GET /review-queue": { status: 200, body: { items: [], next_cursor: null } } },
      () => expect(screen.getByText("Nothing to review.")).toBeTruthy(),
      "reviewer",
    );
  });

  it("review detail", async () => {
    await screenIsClean(
      <BusinessReview businessId="biz-1" />,
      {
        "GET /review-queue/biz-1": { status: 200, body: reviewDetail() },
        "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } },
        "GET /users": { status: 200, body: { items: [me("sales_rep", "rep1@example.com")], next_cursor: null } },
      },
      () => expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy(),
      "reviewer",
    );
  });

  it("leads", async () => {
    await screenIsClean(
      <Leads />,
      { "GET /leads": { status: 200, body: { items: [leadRead()], next_cursor: null } } },
      () => expect(screen.getAllByTestId("lead-row")).toHaveLength(1),
    );
  });

  it("lead detail", async () => {
    await screenIsClean(
      <LeadDetail opportunityId="opp-1" />,
      { "GET /leads/opp-1": { status: 200, body: leadDetail() } },
      () => expect(screen.getByTestId("lead-detail")).toBeTruthy(),
      "sales_rep",
    );
  });

  it("CRM", async () => {
    await screenIsClean(
      <CrmDashboard />,
      {
        "GET /crm/status": { status: 200, body: CRM_STATUS },
        "GET /crm/leads": { status: 200, body: { items: [], next_cursor: null } },
      },
      () => expect(screen.getByTestId("crm-destination")).toBeTruthy(),
      "crm_manager",
    );
  });

  it("searches", async () => {
    await screenIsClean(
      <Searches />,
      {
        "GET /search-jobs/industries": { status: 200, body: [{ key: "plumber", label: "Plumber", query: "plumber" }] },
        "GET /search-jobs": { status: 200, body: { items: [SEARCH_JOB], next_cursor: null } },
        "GET /sources": { status: 200, body: [{ id: "src-1", name: "google_places", kind: "api", enabled: true }] },
        "POST /search-jobs/estimate": { status: 200, body: ESTIMATE },
      },
      () => expect(screen.getAllByTestId("search-row")).toHaveLength(1),
    );
  });

  it("health", async () => {
    await screenIsClean(
      <HealthPage />,
      { "GET /admin/health": { status: 200, body: HEALTH } },
      () => expect(screen.getByTestId("health")).toBeTruthy(),
      "tech_admin",
    );
  });
});
