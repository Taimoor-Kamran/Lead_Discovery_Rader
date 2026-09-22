import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { AiSummaryBox } from "./AiSummaryBox";
import { AI_LABEL } from "@/lib/safe";

describe("AiSummaryBox", () => {
  it("always carries the AI label and renders the summary as text", () => {
    const { container } = render(
      <AiSummaryBox
        ai={{
          ai_generated: true,
          classification_id: "c1",
          model: "scripted-triage",
          prompt_version: "classify-1",
          status: "ok",
          escalated: false,
          business_summary: "<b>Family-run</b> plumber",
          industry: "plumbing",
          industry_matches_listing: true,
          buying_intent: "none_detected",
          unknowns: [],
          created_at: "2026-09-20T10:00:00Z",
        }}
      />,
    );
    expect(screen.getByTestId("ai-label").textContent).toContain(AI_LABEL);
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByTestId("ai-summary").textContent).toContain("<b>Family-run</b> plumber");
    // Model, prompt and status live behind the Details disclosure, not in the headline.
    const details = screen.getByTestId("ai-details");
    const trigger = within(details).getByRole("button", { name: "Details" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(details.textContent).not.toContain("scripted-triage");
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(details.textContent).toContain("scripted-triage");
    expect(details.textContent).toContain("classify-1");
  });

  it("says so when there is no classification", () => {
    render(<AiSummaryBox ai={null} />);
    expect(screen.getByText("No AI classification for this business.")).toBeTruthy();
  });
});
