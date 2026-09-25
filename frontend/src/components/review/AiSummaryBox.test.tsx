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
    expect(screen.queryByTestId("ai-failed")).toBeNull();
  });

  // Spec v0.11.1: a failed call must not look like a business that was never classified.
  it("says a failed classification failed, with its error, instead of 'no classification'", () => {
    render(<AiSummaryBox ai={null} attempt={failedAttempt} />);
    expect(screen.queryByText("No AI classification for this business.")).toBeNull();
    const failed = screen.getByTestId("ai-failed");
    expect(failed.textContent).toContain("AI classification failed");
    expect(failed.textContent).toContain("Call failed");
    expect(failed.textContent).toContain("This business has no AI summary.");
    expect(screen.getByTestId("ai-failed-error").textContent).toBe(
      "OpenAI did not answer: APIConnectionError: Connection error.",
    );
  });

  it("flags a failed attempt newer than the summary it still shows", () => {
    render(<AiSummaryBox ai={answered} attempt={{ ...failedAttempt, created_at: "2026-09-25T01:05:55Z" }} />);
    expect(screen.getByTestId("ai-summary").textContent).toContain("Family-run plumber");
    expect(screen.getByTestId("ai-failed").textContent).toContain("The summary below is from an earlier attempt.");
  });

  it("does not flag a failure the summary has since replaced", () => {
    render(<AiSummaryBox ai={answered} attempt={{ ...failedAttempt, created_at: "2026-09-19T10:00:00Z" }} />);
    expect(screen.queryByTestId("ai-failed")).toBeNull();
  });

  it("does not flag an attempt that was never made", () => {
    render(<AiSummaryBox ai={null} attempt={{ ...failedAttempt, status: "skipped_disabled", error: null }} />);
    expect(screen.queryByTestId("ai-failed")).toBeNull();
    expect(screen.getByText("No AI classification for this business.")).toBeTruthy();
  });
});

const failedAttempt = {
  classification_id: "c2",
  status: "error",
  error: "OpenAI did not answer: APIConnectionError: Connection error.",
  created_at: "2026-09-25T01:05:55Z",
};

const answered = {
  ai_generated: true as const,
  classification_id: "c1",
  model: "scripted-triage",
  prompt_version: "classify-1",
  status: "ok",
  escalated: false,
  business_summary: "Family-run plumber",
  industry: "plumbing",
  industry_matches_listing: true,
  buying_intent: "none_detected",
  unknowns: [],
  created_at: "2026-09-20T10:00:00Z",
};
