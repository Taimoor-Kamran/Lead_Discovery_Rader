import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Leads } from "./Leads";
import { me, renderWithProviders, routeFetch } from "@/test/utils";

const LEAD = {
  opportunity_id: "opp-1",
  business_id: "biz-1",
  business_name: "Barton Creek Plumbing",
  city: "Austin",
  state: "TX",
  industry: "plumbing",
  service: "website_design",
  service_name: "Website design / redesign",
  score: 0.72,
  reason: "Audit found the site is served over http.",
  approved_by: "u",
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
};

afterEach(() => vi.unstubAllGlobals());

describe("Leads page", () => {
  it("shows city and state next to the name, the rep, and only business-level contact", async () => {
    routeFetch({ "GET /leads": { status: 200, body: { items: [LEAD], next_cursor: null } } });
    renderWithProviders(<Leads />, { user: me("sales_rep") });
    await waitFor(() => expect(screen.getAllByTestId("lead-row")).toHaveLength(1));
    expect(screen.getByTestId("lead-place").textContent).toBe("Austin, TX");
    const row = screen.getByTestId("lead-row");
    expect(row.textContent).toContain("rep1@example.com");
    // The stored E.164 number is shown the way people dial it; the raw value stays in the tooltip.
    expect(row.textContent).toContain("(512) 555-0100");
    expect(row.textContent).not.toContain("+15125550100");
    expect(screen.getByTitle("+15125550100")).toBeTruthy();
    // Score as an integer, the service in plain words, the reason on two labelled lines.
    expect(row.textContent).toContain("72");
    expect(row.textContent).not.toContain("0.72");
    expect(row.textContent).toContain("Website redesign");
    expect(screen.getByTestId("rule-reason").textContent).toBe("Audit found the site is served over http.");
    expect(screen.getByTestId("ai-rationale").textContent).toContain("2016 copyright");
    // The name opens the read-only lead page, for a rep too.
    expect(screen.getByRole("link", { name: "Barton Creek Plumbing" }).getAttribute("href")).toBe("/leads/opp-1");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("My leads");
    expect(screen.queryByLabelText("Sales rep")).toBeNull();
  });

  it("lets a reviewer filter by rep", async () => {
    routeFetch({ "GET /leads": { status: 200, body: { items: [LEAD], next_cursor: null } } });
    renderWithProviders(<Leads />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("lead-row")).toHaveLength(1));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Leads");
    expect(screen.getByLabelText("Sales rep").textContent).toContain("rep1@example.com");
  });
});
