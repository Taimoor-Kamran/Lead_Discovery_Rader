import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Searches } from "./Searches";
import { setConfirmRerun } from "./rerun";
import { me, renderWithProviders, routeFetch, router } from "@/test/utils";

const ESTIMATE = {
  max_results: 60,
  uses_places: true,
  places_max_calls: 12,
  places_used_today: 2,
  places_daily_cap: 200,
  places_remaining_today: 198,
  pagespeed_calls: 60,
  pagespeed_used_today: 0,
  pagespeed_daily_cap: 200,
  pagespeed_remaining_today: 200,
  ai_enabled: true,
  ai_provider: "openai",
  ai_calls: 60,
  ai_calls_used_today: 0,
  ai_daily_call_cap: 500,
  ai_budget_usd: 2,
  ai_spent_today_usd: 0.25,
  ai_budget_remaining_usd: 1.75,
  can_run: true,
  blockers: [] as string[],
};

const INDUSTRIES = [
  { key: "dental", label: "Dental", query: "dentist" },
  { key: "plumbing", label: "Plumbing", query: "plumber" },
];
const SOURCES = [{ id: "src-places", name: "google_places", kind: "api", config: {}, enabled: true, created_at: "2026-09-20T00:00:00Z" }];

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    name: "Austin plumbers",
    geo: { city: "Austin", state: "TX" },
    industry: "plumber",
    source_ids: ["src-places"],
    status: "active",
    max_results: 40,
    created_by: "user-admin",
    created_at: "2026-09-20T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    last_run: null,
    ...overrides,
  };
}

function run(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-1",
    search_job_id: "job-1",
    kind: "discovery",
    status: "done",
    progress_total: 40,
    progress_done: 40,
    attempts: 1,
    error: null,
    cancel_requested: false,
    result_summary: { fetched: 40 },
    started_at: "2026-09-20T10:00:00Z",
    finished_at: "2026-09-20T10:05:00Z",
    created_at: "2026-09-20T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => router.push.mockClear());
afterEach(() => vi.unstubAllGlobals());

describe("Searches page", () => {
  it("shows the estimate first and creates then runs a search", async () => {
    const { calls } = routeFetch({
      "GET /search-jobs": { status: 200, body: { items: [], next_cursor: null } },
      "GET /search-jobs/industries": { status: 200, body: INDUSTRIES },
      "GET /sources": { status: 200, body: SOURCES },
      "POST /search-jobs/estimate": { status: 200, body: ESTIMATE },
      "POST /search-jobs": { status: 201, body: job({ id: "job-9", name: "Dental in Austin, TX" }) },
      "POST /search-jobs/job-9/run": { status: 202, body: run({ search_job_id: "job-9", status: "queued" }) },
    });
    renderWithProviders(<Searches />, { user: me("sales_rep") });

    await waitFor(() => expect(screen.getByTestId("estimate")).toBeTruthy());
    // The enforced safety limit, shown as "at most N" — never a "≈" guess.
    expect(screen.getByTestId("est-places").querySelector(".font-mono")?.textContent).toBe("12");
    expect(screen.getByTestId("estimate").textContent).toContain("Google Places calls (at most)");
    expect(screen.getByTestId("est-places").textContent).toContain("198 of 200 left today");
    expect(screen.getByTestId("est-ai").textContent).toContain("$1.75 of $2.00");

    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(1));
    fireEvent.change(screen.getByLabelText("Industry"), { target: { value: "dental" } });
    fireEvent.change(screen.getByLabelText("City"), { target: { value: "Austin" } });
    fireEvent.change(screen.getByLabelText("State"), { target: { value: "TX" } });
    fireEvent.change(screen.getByLabelText("Max results"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and run" }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/searches/job-9"));
    const created = calls.find((call) => call.method === "POST" && call.url.endsWith("/search-jobs"));
    expect(JSON.parse(String(created?.init?.body))).toEqual({
      name: "Dental in Austin, TX",
      industry: "dentist",
      geo: { city: "Austin", state: "TX" },
      source_ids: ["src-places"],
      status: "active",
      max_results: 45,
    });
    expect(calls.some((call) => call.url.endsWith("/search-jobs/job-9/run"))).toBe(true);
  });

  it("disables Run when the daily cap would be exceeded and shows why", async () => {
    routeFetch({
      "GET /search-jobs": { status: 200, body: { items: [], next_cursor: null } },
      "GET /search-jobs/industries": { status: 200, body: INDUSTRIES },
      "GET /sources": { status: 200, body: SOURCES },
      "POST /search-jobs/estimate": {
        status: 200,
        body: { ...ESTIMATE, places_remaining_today: 1, can_run: false, blockers: ["This run may make up to 12 Google Places call(s) but only 1 of today's 200 remain."] },
      },
    });
    renderWithProviders(<Searches />, { user: me("admin") });

    await waitFor(() => expect(screen.getByTestId("est-blockers")).toBeTruthy());
    expect(screen.getByTestId("est-blockers").textContent).toContain("only 1 of today's 200 remain");
    expect((screen.getByRole("button", { name: "Save and run" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("lists past searches with their last run and warns before a re-run within 7 days", async () => {
    const recent = run({ finished_at: new Date().toISOString() });
    const { calls } = routeFetch({
      "GET /search-jobs": { status: 200, body: { items: [job({ last_run: recent }), job({ id: "job-2", name: "Old one", last_run: run({ finished_at: "2026-01-01T00:00:00Z" }) })], next_cursor: null } },
      "GET /search-jobs/industries": { status: 200, body: INDUSTRIES },
      "GET /sources": { status: 200, body: SOURCES },
      "POST /search-jobs/estimate": { status: 200, body: ESTIMATE },
      "POST /search-jobs/job-1/run": { status: 202, body: run({ status: "queued" }) },
      "POST /search-jobs/job-2/run": { status: 202, body: run({ status: "queued" }) },
    });
    const asked: number[] = [];
    setConfirmRerun(() => {
      asked.push(1);
      return false;
    });
    renderWithProviders(<Searches />, { user: me("sales_rep") });

    await waitFor(() => expect(screen.getAllByTestId("search-row")).toHaveLength(2));
    expect(screen.getAllByTestId("last-run")[0].textContent).toContain("done");

    fireEvent.click(screen.getAllByRole("button", { name: "Run again" })[0]);
    expect(asked).toHaveLength(1);
    expect(calls.some((call) => call.url.endsWith("/job-1/run"))).toBe(false);

    fireEvent.click(screen.getAllByRole("button", { name: "Run again" })[1]);
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/job-2/run"))).toBe(true));
    expect(asked).toHaveLength(1);
    setConfirmRerun(() => true);
  });

  it("is read-only for a tech admin", async () => {
    routeFetch({ "GET /search-jobs": { status: 200, body: { items: [job()], next_cursor: null } } });
    renderWithProviders(<Searches />, { user: me("tech_admin") });

    await waitFor(() => expect(screen.getAllByTestId("search-row")).toHaveLength(1));
    expect(screen.queryByRole("form", { name: "New search" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Run/ })).toBeNull();
    expect(screen.getByText(/can follow searches but not create/)).toBeTruthy();
  });
});
