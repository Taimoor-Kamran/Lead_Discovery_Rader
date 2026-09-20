import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { OpportunityCard } from "./OpportunityCard";
import { reviewOpportunity } from "@/test/utils";
import { AI_LABEL } from "@/lib/safe";

function card(overrides: Parameters<typeof reviewOpportunity>[0] = {}, canDecide = true) {
  return render(
    <OpportunityCard
      opportunity={reviewOpportunity(overrides)}
      focused={false}
      canDecide={canDecide}
      busy={false}
      onFocus={() => {}}
      onDecide={vi.fn()}
      onUndo={vi.fn()}
    />,
  );
}

describe("OpportunityCard display", () => {
  it("shows the service in plain words and one score number, confidence in the tooltip", () => {
    card();
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Website redesign");
    expect(screen.getByRole("heading", { level: 3 }).getAttribute("title")).toBe("website_design");
    const chip = screen.getByTestId("score-chip");
    expect(chip.textContent).toBe("Score 72");
    expect(chip.getAttribute("title")).toContain("Confidence 80%");
  });

  it("puts the rule reason and the AI rationale on two labelled lines", () => {
    card({
      source: "rules+ai",
      reason: "Audit found the site is served over http. The model also read a 2016 copyright line.",
      rule_reason: "Audit found the site is served over http.",
      ai_rationale: "The model also read a 2016 copyright line.",
    });
    expect(screen.getByTestId("rule-reason").textContent).toBe("Audit found the site is served over http.");
    expect(screen.getByTestId("ai-rationale").textContent).toContain("The model also read a 2016 copyright line.");
    // The stored one-paragraph reason is not shown a second time.
    expect(screen.queryByText("Audit found the site is served over http. The model also read a 2016 copyright line.")).toBeNull();
  });

  it("lists a finding once with an 'AI agrees' badge when rules and AI both cite it", () => {
    card({
      source: "rules+ai",
      evidence: [
        { finding_code: "no_https", text: "served over http", url: "https://x.invalid/", source: "rules" },
        { finding_code: "no_https", text: "Audit found the homepage served over http, not https.", url: "https://x.invalid/", source: "ai" },
        { finding_code: "stale_copyright", text: "2016 Barton Creek Plumbing LLC", url: "https://x.invalid/", source: "ai" },
      ],
    });
    const items = screen.getAllByTestId("evidence");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("No HTTPS");
    expect(items[0].textContent).toContain("served over http");
    expect(items[0].querySelector("[data-testid='ai-agrees']")).not.toBeNull();
    expect(items[0].textContent).toContain("AI quote:");
    expect(items[1].textContent).toContain("Outdated copyright year");
    expect(items[1].querySelector("[data-testid='ai-agrees']")).toBeNull();
    expect(items[1].querySelector("[data-testid='ai-label']")).not.toBeNull();
  });

  it("folds the AI provenance into a Details disclosure and keeps the decisions at the top of the card", () => {
    card({
      source: "rules+ai",
      ai: {
        classification_id: "c1",
        model: "scripted-triage",
        prompt_version: "classify-1",
        status: "ok",
        escalated: false,
        buying_intent: null,
        business_summary: null,
      },
    });
    const details = screen.getByTestId("ai-details");
    expect(details.tagName).toBe("DETAILS");
    expect(details.querySelector("summary")?.textContent).toBe("Details");
    expect(details.textContent).toContain("scripted-triage");
    const top = screen.getByTestId("card-top");
    expect(top.className).toContain("sticky");
    expect(top.querySelector("[aria-label='Decisions']")).not.toBeNull();
    expect(screen.getByTestId("opportunity-card").id).toBe("opportunity-opp-1");
  });
});

describe("OpportunityCard safety rules", () => {
  it("renders <script> in evidence as text, never as markup", () => {
    const { container } = card({
      evidence: [{ finding_code: "no_h1", text: "<script>alert(1)</script> no heading", url: "https://x.invalid/", source: "rules" }],
    });
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByTestId("evidence").textContent).toContain("<script>alert(1)</script> no heading");
    expect(container.innerHTML).not.toContain("<script>");
  });

  it("never turns a javascript: evidence URL into a link", () => {
    const { container } = card({
      evidence: [{ finding_code: "no_h1", text: "x", url: "javascript:alert(1)", source: "rules" }],
    });
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("evidence").textContent).toContain("javascript:alert(1)");
  });

  it("links an https evidence URL in a new tab with noopener", () => {
    card();
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("https://bartoncreekplumbing.invalid/");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("labels an AI-only opportunity and AI evidence", () => {
    card({
      source: "ai",
      evidence: [{ finding_code: "no_h1", text: "quote", url: "https://x.invalid/", source: "ai" }],
    });
    const labels = screen.getAllByTestId("ai-label");
    expect(labels.length).toBeGreaterThanOrEqual(2);
    expect(labels[0].textContent).toContain(AI_LABEL);
  });

  it("labels a rules+ai opportunity too, and not a rules-only one", () => {
    const { unmount } = card({ source: "rules+ai" });
    expect(screen.getAllByTestId("ai-label")).toHaveLength(1);
    unmount();
    card({ source: "rules" });
    expect(screen.queryByTestId("ai-label")).toBeNull();
  });

  it("shows decision buttons only for open rows and deciders", () => {
    const { unmount } = card();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    unmount();
    card({ review_status: "approved" });
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    card({}, false);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("shows the four score components and the history with undo where allowed", () => {
    card({
      history: [
        {
          id: "dec-1",
          opportunity_id: "opp-1",
          decision: "reject",
          from_status: "pending",
          to_status: "rejected",
          reason_code: "ai_mistake",
          note: null,
          duplicate_of: null,
          assigned_to: null,
          assigned_to_email: null,
          decided_by: "u1",
          decided_by_email: "reviewer@example.com",
          decided_at: "2026-09-20T10:00:00Z",
          undone_at: null,
          undone_by: null,
          undo_until: "2026-09-20T10:30:00Z",
          can_undo: true,
        },
      ],
    });
    expect(screen.getByLabelText("Facts 50%")).toBeTruthy();
    expect(screen.getByLabelText("Contactability 100%")).toBeTruthy();
    expect(screen.getByTestId("history-row").textContent).toContain("AI mistake");
    expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
  });
});
