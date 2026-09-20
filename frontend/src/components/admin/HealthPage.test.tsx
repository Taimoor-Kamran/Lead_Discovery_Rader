import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HealthPage } from "./HealthPage";
import { me, renderWithProviders, routeFetch } from "@/test/utils";

const ALERT = {
  id: "al-1",
  rule: "crm_held",
  severity: "warning",
  message: "2 CRM lead(s) are held and need a human on the CRM page",
  details: {},
  first_seen_at: "2026-09-20T09:00:00Z",
  last_seen_at: "2026-09-20T10:00:00Z",
  acknowledged_at: null,
  acknowledged_by: null,
  cleared_at: null,
  active: true,
  acknowledged: false,
};

const REPORT = {
  generated_at: "2026-09-20T10:00:00Z",
  environment: "local",
  db: true,
  redis: true,
  jobs: {
    last_24h: [{ kind: "discovery", done: 1, failed: 1, cancelled: 0, total: 2, success_rate: 0.5 }],
    last_7d: [{ kind: "discovery", done: 5, failed: 1, cancelled: 0, total: 6, success_rate: 0.8333 }],
  },
  sources: [{ source: "google_places", calls: 4, errors: 2, error_rate: 0.5 }],
  timings: [{ kind: "discovery", runs: 1, median_seconds: 12.3, p95_seconds: 12.3 }],
  queue: {
    name: "default",
    length: 0,
    scheduler_lock_held: true,
    schedule: [{ name: "backup", cron: "0 2 * * *", description: "pg_dump", last_fired_at: null, last_run_status: null, last_run_finished_at: null }],
  },
  ai: { provider: "fake", calls_today: 3, call_cap: 500, classifications_today: 4, reused_today: 1, reuse_rate: 0.25, spent_today_usd: 0, budget_usd: 2, budget_ratio: 0.006, prices_configured: false },
  data_quality: { records_total: 20, records_invalid: 1, invalid_rate: 0.05, businesses_total: 19, missing_city: 0, missing_phone: 2, missing_website: 3 },
  duplicates: { auto_merged: 2, sent_to_review: 1, merged_by_review: 0, kept_apart: 1, pending_review: 0 },
  crm: { destination: "fake", scheduled: 1, held: 2, synced_today: 0 },
  freshness: [{ source: "google_places", enabled: true, records: 20, last_discovered_at: "2026-09-20T08:00:00Z" }],
  audits: { done: 15, robots_blocked: 1, unreachable: 2, failed: 0, skipped: 1, total: 19 },
  backups: { directory: "/app/backups", backups_kept: 3, keep: 14, last_backup_file: "radar-20260920-020000.dump", last_backup_at: "2026-09-20T02:00:00Z", last_backup_size_bytes: 1024, last_verify_at: "2026-09-20T04:00:00Z", last_verify_ok: true, last_verify_file: "radar-20260920-020000.dump", last_verify_error: null },
  thresholds: { job_success_rate_min: 0.8, source_error_rate_max: 0.2, ai_budget_ratio: 0.8, backup_max_age_hours: 36, queue_length_max: 500, watchdog_stale_minutes: 30 },
  alerts: [ALERT],
};

afterEach(() => vi.unstubAllGlobals());

describe("Health page", () => {
  it("shows every metric block and lets an admin acknowledge an alert", async () => {
    const { calls } = routeFetch({
      "GET /admin/health": { status: 200, body: REPORT },
      "POST /admin/alerts/al-1/acknowledge": { status: 200, body: { ...ALERT, acknowledged: true, acknowledged_at: "2026-09-20T10:01:00Z" } },
    });
    renderWithProviders(<HealthPage />, { user: me("tech_admin") });

    await waitFor(() => expect(screen.getByTestId("health")).toBeTruthy());
    for (const block of ["m-jobs", "m-sources", "m-timings", "m-queue", "m-ai", "m-quality", "m-duplicates", "m-crm", "m-freshness", "m-audits", "m-backups"]) {
      expect(screen.getByTestId(block)).toBeTruthy();
    }
    expect(screen.getByTestId("m-jobs").textContent).toContain("50% (1/2)");
    expect(screen.getByTestId("m-sources").textContent).toContain("google_places: 50% (2 of 4 calls)");
    expect(screen.getByTestId("m-timings").textContent).toContain("median 12.3 s");
    expect(screen.getByTestId("m-ai").textContent).toContain("reuse rate 25%");
    expect(screen.getByTestId("m-crm").textContent).toContain("held 2");
    expect(screen.getByTestId("m-backups").textContent).toContain("radar-20260920-020000.dump");
    expect(screen.getByTestId("m-backups").textContent).toContain("OK");

    const alert = screen.getByTestId("alert");
    expect(alert.textContent).toContain("2 CRM lead(s) are held");
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/admin/alerts/al-1/acknowledge"))).toBe(true));
  });

  it("says so when there are no alerts", async () => {
    routeFetch({ "GET /admin/health": { status: 200, body: { ...REPORT, alerts: [] } } });
    renderWithProviders(<HealthPage />, { user: me("admin") });
    await waitFor(() => expect(screen.getByTestId("no-alerts")).toBeTruthy());
  });
});
