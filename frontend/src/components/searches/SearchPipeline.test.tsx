import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { SearchPipeline, stillWorking } from "./SearchPipeline";
import type { JobRunRead, PipelineRead } from "@/lib/api";
import { me, renderWithProviders, routeFetch } from "@/test/utils";

const JOB = {
  id: "job-1",
  name: "Austin plumbers",
  geo: { city: "Austin", state: "TX" },
  industry: "plumber",
  source_ids: [],
  status: "active",
  max_results: null,
  created_by: "user-admin",
  created_at: "2026-09-20T00:00:00Z",
  updated_at: "2026-09-20T00:00:00Z",
};

function run(kind: string, status: JobRunRead["status"], extra: Partial<JobRunRead> = {}): JobRunRead {
  return {
    id: `run-${kind}`,
    search_job_id: "job-1",
    kind,
    status,
    progress_total: 12,
    progress_done: 12,
    attempts: 1,
    error: null,
    cancel_requested: false,
    result_summary: null,
    started_at: "2026-09-20T10:00:00Z",
    finished_at: status === "done" || status === "failed" ? "2026-09-20T10:05:00Z" : null,
    created_at: "2026-09-20T10:00:00Z",
    ...extra,
  };
}

const PIPELINE: PipelineRead = {
  search_job_id: "job-1",
  discovery_run_id: "run-discovery",
  stages: [
    { stage: "discovery", run: run("discovery", "done"), counts: { fetched: 12, stored_new: 10, updated: 2, invalid: 0 }, error: null },
    { stage: "resolution", run: run("resolution", "done"), counts: { processed: 12, created: 9, linked_existing: 3 }, error: null },
    { stage: "audit", run: run("audit", "failed"), counts: {}, error: "QuotaExceededError: PageSpeed cap" },
    { stage: "classification", run: null, counts: {}, error: null },
  ],
  review_queue_query: { city: "Austin" },
};

afterEach(() => vi.unstubAllGlobals());

describe("Search pipeline view", () => {
  it("shows the four stages with status, counts, errors and the review-queue link", async () => {
    routeFetch({
      "GET /search-jobs/job-1": { status: 200, body: JOB },
      "GET /search-jobs/job-1/pipeline": { status: 200, body: PIPELINE },
      "GET /search-jobs/job-1/estimate": { status: 200, body: { max_results: 60, uses_places: true, places_calls: 3, places_used_today: 0, places_daily_cap: 200, places_remaining_today: 200, pagespeed_calls: 60, pagespeed_used_today: 0, pagespeed_daily_cap: 200, pagespeed_remaining_today: 200, ai_enabled: false, ai_provider: "disabled", ai_calls: 0, ai_calls_used_today: 0, ai_daily_call_cap: 500, ai_budget_usd: 2, ai_spent_today_usd: 0, ai_budget_remaining_usd: 2, can_run: true, blockers: [] } },
    });
    renderWithProviders(<SearchPipeline searchJobId="job-1" />, { user: me("sales_rep") });

    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Austin plumbers"));
    expect(screen.getByTestId("stage-discovery").querySelector('[data-testid="stage-status"]')?.textContent).toBe("done");
    expect(screen.getByTestId("count-discovery-stored_new").textContent).toBe("10");
    expect(screen.getByTestId("count-resolution-created").textContent).toBe("9");
    expect(screen.getByTestId("stage-audit").textContent).toContain("QuotaExceededError: PageSpeed cap");
    expect(screen.getByTestId("stage-classification").querySelector('[data-testid="stage-status"]')?.textContent).toBe("not started");
    expect(screen.getByTestId("review-link").getAttribute("href")).toBe("/review?city=Austin");
    expect(screen.getByTestId("est-ai").textContent).toContain("AI is off");
    expect(screen.getByRole("button", { name: "Run again" })).toBeTruthy();
  });

  it("knows when the pipeline is still moving", () => {
    expect(stillWorking(PIPELINE)).toBe(false);
    expect(stillWorking({ ...PIPELINE, stages: [{ stage: "audit", run: run("audit", "running"), counts: {}, error: null }] })).toBe(true);
    expect(stillWorking(null)).toBe(false);
  });
});
