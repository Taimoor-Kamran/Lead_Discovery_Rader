/**
 * Rules-only is a supported configuration, not a fallback (spec v0.10.0 §3), so with
 * `AI_PROVIDER=disabled` the pages must show **no AI furniture** — no summary box, no
 * rationale line, no AI details disclosure, no AI label — rather than an empty frame where
 * an AI answer would have gone.
 *
 * Both directions are tested. Switching AI off must not become a one-way door: with the
 * provider on, everything still renders exactly as it does today.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { HealthPage } from "@/components/admin/HealthPage";
import { LeadDetail } from "@/components/leads/LeadDetail";
import { EstimatePanel } from "@/components/searches/EstimatePanel";
import { BusinessReview } from "@/components/review/BusinessReview";
import { SOURCE_LABELS } from "@/lib/labels";
import { AI_LABEL } from "@/lib/safe";
import {
  leadDetail,
  me,
  queueItem,
  renderWithProviders,
  reviewDetail,
  reviewOpportunity,
  routeFetch,
} from "@/test/utils";

afterEach(() => vi.unstubAllGlobals());

const AI = {
  ai_generated: true as const,
  classification_id: "cls-1",
  model: "gpt-4.1-mini",
  prompt_version: "classify-1",
  status: "ok",
  escalated: false,
  business_summary: "A plumbing company in Austin.",
  industry: "plumbing",
  industry_matches_listing: true,
  buying_intent: "none_detected",
  unknowns: ["whether they take card payments"],
  created_at: "2026-09-20T10:00:00Z",
};

/** What a rules-only run stores: source `rules`, no classification, no rationale. */
const RULES_ONLY = reviewOpportunity({
  source: "rules",
  ai: null,
  ai_classification_id: null,
  ai_rationale: null,
});

/** What a run with the model on stores for the same business. */
const WITH_AI = reviewOpportunity({
  source: "rules+ai",
  ai: AI as never,
  ai_classification_id: "cls-1",
  ai_rationale: "The model also read a 2016 copyright line.",
});

async function reviewPage(detail: ReturnType<typeof reviewDetail>) {
  routeFetch({
    "GET /review-queue/biz-1": { status: 200, body: detail },
    "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } },
    "GET /users": { status: 200, body: { items: [], next_cursor: null } },
  });
  const rendered = renderWithProviders(<BusinessReview businessId="biz-1" />, {
    user: me("reviewer"),
  });
  await waitFor(() => expect(screen.getByTestId("business-facts")).toBeTruthy());
  return rendered;
}

describe("with AI disabled, no AI element renders", () => {
  it("shows no summary box, no rationale, no details disclosure and no AI label", async () => {
    const { container } = await reviewPage(
      reviewDetail({ ai_enabled: false, ai: null, opportunities: [RULES_ONLY] }),
    );

    expect(screen.queryByTestId("ai-summary")).toBeNull();
    expect(screen.queryByTestId("ai-rationale")).toBeNull();
    expect(screen.queryByTestId("ai-details")).toBeNull();
    expect(screen.queryByTestId("ai-label")).toBeNull();
    // Not even the "no AI classification for this business" placeholder: with the layer off
    // there is nothing missing, so there is nothing to apologise for.
    expect(container.textContent).not.toContain("No AI classification");
    expect(container.textContent).not.toContain(AI_LABEL);
  });

  it("reads the opportunity's source as Rules on a rules-only run", async () => {
    await reviewPage(reviewDetail({ ai_enabled: false, ai: null, opportunities: [RULES_ONLY] }));

    const card = screen.getByTestId("opportunity-card");
    expect(card.textContent).toContain(SOURCE_LABELS.rules);
    expect(card.textContent).not.toContain(SOURCE_LABELS["rules+ai"]);
  });

  it("still states the recorded source of a claim the model did help make", async () => {
    /**
     * The badge answers "where did this claim come from", and a claim made when the model
     * was on came from rules + AI whatever the switch says today. Rewriting that to "Rules"
     * would be inventing provenance, which the blueprint forbids — so the badge keeps the
     * stored source while the model's own output stays hidden.
     */
    await reviewPage(reviewDetail({ ai_enabled: false, ai: AI, opportunities: [WITH_AI] }));

    expect(screen.getByTestId("opportunity-card").textContent).toContain(SOURCE_LABELS["rules+ai"]);
    expect(screen.queryByTestId("ai-summary")).toBeNull();
    expect(screen.queryByTestId("ai-rationale")).toBeNull();
    expect(screen.queryByTestId("ai-details")).toBeNull();
  });

  it("hides the AI rationale on a lead as well", async () => {
    routeFetch({
      "GET /leads/opp-1": {
        status: 200,
        body: leadDetail({ ai_enabled: false, opportunity: WITH_AI }),
      },
    });
    renderWithProviders(<LeadDetail opportunityId="opp-1" />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getByTestId("lead-detail")).toBeTruthy());

    expect(screen.queryByTestId("ai-rationale")).toBeNull();
    expect(screen.getByTestId("rule-reason")).toBeTruthy();
  });
});

describe("with AI enabled, everything behaves as it does today", () => {
  it("shows the summary box, the rationale line and the AI label", async () => {
    await reviewPage(reviewDetail({ ai_enabled: true, ai: AI, opportunities: [WITH_AI] }));

    expect(screen.getByTestId("ai-summary")).toBeTruthy();
    expect(screen.getByTestId("ai-rationale")).toBeTruthy();
    expect(screen.getAllByTestId("ai-label").length).toBeGreaterThan(0);
    expect(screen.getByTestId("opportunity-card").textContent).toContain(SOURCE_LABELS["rules+ai"]);
  });

  it("still says so when the layer is on but this business was never classified", async () => {
    await reviewPage(reviewDetail({ ai_enabled: true, ai: null, opportunities: [RULES_ONLY] }));

    expect(screen.getByText("No AI classification for this business.")).toBeTruthy();
  });
});

// --- the two places that quote the AI budget ----------------------------------------------

const HEALTH_AI = {
  provider: "disabled",
  calls_today: 0,
  call_cap: 500,
  classifications_today: 0,
  reused_today: 0,
  reuse_rate: null,
  spent_today_usd: 0,
  budget_usd: 2,
  budget_ratio: 0,
  prices_configured: false,
};

const HEALTH = {
  generated_at: "2026-09-20T10:00:00Z",
  environment: "production",
  db: true,
  redis: true,
  jobs: { last_24h: [], last_7d: [] },
  sources: [],
  timings: [],
  queue: { name: "default", length: 0, scheduler_lock_held: true, schedule: [] },
  ai: HEALTH_AI,
  data_quality: {
    records_total: 0,
    records_invalid: 0,
    invalid_rate: null,
    businesses_total: 0,
    missing_city: 0,
    missing_phone: 0,
    missing_website: 0,
  },
  duplicates: {
    auto_merged: 0,
    sent_to_review: 0,
    merged_by_review: 0,
    kept_apart: 0,
    pending_review: 0,
  },
  crm: { destination: "csv", scheduled: 0, held: 0, synced_today: 0 },
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
  alerts: [],
};

describe("/admin/health shows AI as off rather than as zero of a budget", () => {
  it("says AI is off and quotes no call count or budget", async () => {
    routeFetch({ "GET /admin/health": { status: 200, body: HEALTH } });
    renderWithProviders(<HealthPage />, { user: me("admin") });
    await waitFor(() => expect(screen.getByTestId("m-ai")).toBeTruthy());

    const block = screen.getByTestId("m-ai");
    expect(screen.getByTestId("ai-off")).toBeTruthy();
    expect(block.textContent).toContain("AI is off");
    expect(block.textContent).not.toContain("of 500 calls");
    expect(block.textContent).not.toContain("$2.00");
  });

  it("reports the numbers as before when a provider is configured", async () => {
    routeFetch({
      "GET /admin/health": {
        status: 200,
        body: { ...HEALTH, ai: { ...HEALTH_AI, provider: "openai", calls_today: 12 } },
      },
    });
    renderWithProviders(<HealthPage />, { user: me("admin") });
    await waitFor(() => expect(screen.getByTestId("m-ai")).toBeTruthy());

    expect(screen.queryByTestId("ai-off")).toBeNull();
    expect(screen.getByTestId("m-ai").textContent).toContain("12 of 500 calls");
  });
});

const ESTIMATE = {
  max_results: 20,
  uses_places: true,
  places_calls: 1,
  places_used_today: 1,
  places_daily_cap: 200,
  places_remaining_today: 199,
  pagespeed_calls: 20,
  pagespeed_used_today: 0,
  pagespeed_daily_cap: 200,
  pagespeed_remaining_today: 200,
  ai_enabled: false,
  ai_provider: "disabled",
  ai_calls: 0,
  ai_calls_used_today: 0,
  ai_daily_call_cap: 500,
  ai_budget_usd: 2,
  ai_spent_today_usd: 0,
  ai_budget_remaining_usd: 2,
  can_run: true,
  blockers: [],
};

describe("the search cost estimate shows AI as off", () => {
  it("names no budget when AI is disabled", () => {
    render(<EstimatePanel estimate={ESTIMATE} />);

    const line = screen.getByTestId("est-ai");
    expect(line.textContent).toContain("AI is off");
    expect(line.textContent).not.toContain("budget left today");
  });

  it("still quotes the budget when AI is enabled", () => {
    render(
      <EstimatePanel
        estimate={{ ...ESTIMATE, ai_enabled: true, ai_provider: "openai", ai_calls: 20 }}
      />,
    );

    const line = screen.getByTestId("est-ai");
    expect(line.textContent).toContain("via openai");
    expect(line.textContent).toContain("budget left today");
  });
});
