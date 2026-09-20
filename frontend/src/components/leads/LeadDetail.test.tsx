import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { LeadDetail, NOT_YOURS_MESSAGE } from "./LeadDetail";
import { envelope, me, renderWithProviders, reviewDetail, reviewOpportunity, routeFetch } from "@/test/utils";
import { AI_LABEL } from "@/lib/safe";

const APPROVAL = {
  id: "dec-1",
  opportunity_id: "opp-1",
  decision: "approve" as const,
  from_status: "pending" as const,
  to_status: "approved" as const,
  reason_code: null,
  note: "Owner already asked for a quote",
  duplicate_of: null,
  assigned_to: "user-sales_rep",
  assigned_to_email: "rep1@example.com",
  decided_by: "user-reviewer",
  decided_by_email: "reviewer@example.com",
  decided_at: "2026-09-20T10:00:00Z",
  undone_at: null,
  undone_by: null,
  undo_until: "2026-09-20T10:30:00Z",
  can_undo: false,
};

function detail() {
  const base = reviewDetail();
  return {
    lead: {
      opportunity_id: "opp-1",
      business_id: "biz-1",
      business_name: "Barton Creek Plumbing",
      city: "Austin",
      state: "TX",
      industry: "plumbing",
      service: "website_design",
      service_name: "Website design / redesign",
      score: 0.72,
      reason: "Audit found the site is served over http. The model also read a 2016 copyright line.",
      approved_by: "user-reviewer",
      approved_by_email: "reviewer@example.com",
      approved_at: "2026-09-20T10:00:00Z",
      assigned_to: "user-sales_rep",
      assigned_to_email: "rep1@example.com",
      phone_e164: "+15125550100",
      website: "https://bartoncreekplumbing.invalid/",
      lock_version: 1,
      top_evidence: null,
      rule_reason: "Audit found the site is served over http.",
      ai_rationale: "The model also read a 2016 copyright line.",
    },
    business: base.business,
    audit: {
      id: "audit-1",
      business_id: "biz-1",
      job_run_id: null,
      url_audited: "https://bartoncreekplumbing.invalid/",
      final_url: null,
      status: "done" as const,
      http_status: 200,
      finding_codes: ["no_https"],
      rules_version: "audit-1",
      started_at: null,
      finished_at: null,
      created_at: "2026-09-20T09:00:00Z",
      checks: {},
      psi: null,
      tech_stack: {},
      findings: [
        { code: "no_https", severity: "high", message: "Audit found the homepage served over http, not https.", evidence_text: "<script>x</script>", evidence_url: "javascript:alert(1)" },
      ],
      page_text: null,
      page_text_hidden: true,
      html_sha256: null,
      content_expires_at: null,
      purged_at: null,
    },
    opportunity: reviewOpportunity({
      source: "rules+ai",
      review_status: "approved",
      lock_version: 1,
      rule_reason: "Audit found the site is served over http.",
      ai_rationale: "The model also read a 2016 copyright line.",
      history: [APPROVAL],
      evidence: [
        { finding_code: "no_https", text: "served over http", url: "https://bartoncreekplumbing.invalid/", source: "rules" },
        { finding_code: "no_https", text: "Audit found the homepage served over http, not https.", url: "https://bartoncreekplumbing.invalid/", source: "ai" },
      ],
    }),
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("LeadDetail", () => {
  it("shows the business, the plain-language reasons and findings, and who approved it — read-only", async () => {
    const { container } = routeFetch({ "GET /leads/opp-1": { status: 200, body: detail() } }) && renderWithProviders(<LeadDetail opportunityId="opp-1" />, { user: me("sales_rep") });
    await waitFor(() => expect(screen.getByTestId("lead-detail")).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Barton Creek Plumbing");
    expect(screen.getByText("Website redesign")).toBeTruthy();
    expect(screen.getByTestId("score-chip").textContent).toBe("Score 72");
    expect(screen.getByText("(512) 555-0100")).toBeTruthy();
    expect(screen.getByTestId("rule-reason").textContent).toBe("Audit found the site is served over http.");
    expect(screen.getByTestId("ai-rationale").textContent).toContain("2016 copyright");
    expect(screen.getAllByTestId("ai-label")[0].textContent).toContain(AI_LABEL);
    // One evidence entry for the finding both cited, with the badge.
    expect(screen.getAllByTestId("evidence")).toHaveLength(1);
    expect(screen.getByTestId("ai-agrees")).toBeTruthy();
    expect(screen.getByTestId("finding").textContent).toContain("No HTTPS");
    const approval = screen.getByTestId("approval");
    expect(approval.textContent).toContain("reviewer@example.com");
    expect(approval.textContent).toContain("rep1@example.com");
    expect(approval.textContent).toContain("Owner already asked for a quote");
    // Read-only: no decision buttons at all, and unsafe content stays text.
    expect(screen.queryByRole("button")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
  });

  it("tells a rep when a lead is not theirs (403) and offers the way back", async () => {
    routeFetch({ "GET /leads/opp-9": { status: 403, body: envelope(403, "forbidden", "This lead is not assigned to you") } });
    renderWithProviders(<LeadDetail opportunityId="opp-9" />, { user: me("sales_rep") });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain(NOT_YOURS_MESSAGE));
    expect(screen.getByRole("link", { name: "Back to leads" }).getAttribute("href")).toBe("/leads");
  });

  it("says when a lead does not exist", async () => {
    routeFetch({ "GET /leads/opp-0": { status: 404, body: envelope(404, "not_found", "Lead not found") } });
    renderWithProviders(<LeadDetail opportunityId="opp-0" />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Lead not found."));
  });
});
