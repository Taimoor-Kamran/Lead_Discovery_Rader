/**
 * The acceptance criteria of v0.9.0 that are about *shape* rather than tokens: the
 * evidence typeface, the two-column review layout with its sticky decision panel, and
 * findings as one severity list rather than a stack of cards. They live here, next to the
 * token and contrast audits, because they are claims about the design pass as a whole.
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { EvidenceList } from "@/components/review/EvidenceList";
import { FindingList } from "@/components/review/FindingList";
import { ReasonLines } from "@/components/review/ReasonLines";
import { SafeLink } from "@/components/SafeLink";
import { BusinessReview } from "@/components/review/BusinessReview";
import { me, queueItem, renderWithProviders, reviewDetail, routeFetch } from "@/test/utils";

const FINDINGS = [
  { code: "no_h1", severity: "low", message: "Audit found no main heading.", evidence_text: "No <h1> with text", evidence_url: null },
  { code: "no_https", severity: "high", message: "Audit found the site served over http.", evidence_text: "served over http", evidence_url: "https://x.invalid/" },
];

describe("evidence gets the evidence typeface, our own wording does not", () => {
  it("sets a quoted finding in the mono face and the audit's own sentence in the sans", () => {
    render(<FindingList findings={FINDINGS} />);
    const quote = screen.getByText("served over http");
    expect(quote.className).toContain("font-mono");
    // The tool's own wording — the message line — stays in the UI face.
    expect(screen.getByText("Audit found the site served over http.").className).not.toContain("font-mono");
  });

  it("sets a quoted evidence block in the mono face", () => {
    render(
      <EvidenceList
        items={[{ finding_code: "no_https", text: "served over http", url: "https://x.invalid/", source: "rules" }]}
      />,
    );
    expect(screen.getByText("served over http").className).toContain("font-mono");
  });

  it("sets a URL in the mono face, whether or not it is a link", () => {
    const { rerender } = render(<SafeLink href="https://x.invalid/" />);
    expect(screen.getByRole("link").className).toContain("font-mono");
    rerender(<SafeLink href="javascript:alert(1)" />);
    expect(screen.getByText("javascript:alert(1)").className).toContain("font-mono");
  });

  it("leaves our own reasons in the UI face", () => {
    render(<ReasonLines ruleReason="Audit found the site is served over http." aiRationale={null} />);
    expect(screen.getByTestId("rule-reason").className).not.toContain("font-mono");
  });
});

describe("findings are a severity list, not stacked cards", () => {
  it("is one list, one item per finding, each with a severity word", () => {
    render(<FindingList findings={FINDINGS} />);
    const items = screen.getAllByTestId("finding");
    expect(items).toHaveLength(2);
    for (const item of items) {
      expect(item.tagName).toBe("LI");
      expect(item.closest("ul")).not.toBeNull();
      // No card: a finding carries no surface of its own.
      expect(item.className).not.toContain("rounded-lg");
      expect(item.className).not.toContain("bg-surface");
    }
    // Ranked by severity: the high one leads, whatever order the audit stored them in.
    expect(items[0].textContent).toContain("No HTTPS");
    expect(items[0].textContent).toContain("High");
  });
});

describe("review detail is a two-column argument with a sticky decision panel", () => {
  it("puts the case in one column and the decision in the other, and makes the decision stick", async () => {
    routeFetch({
      "GET /review-queue/biz-1": { status: 200, body: reviewDetail() },
      "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } },
      "GET /users": { status: 200, body: { items: [me("sales_rep", "rep1@example.com")], next_cursor: null } },
    });
    renderWithProviders(<BusinessReview businessId="biz-1" />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy());

    const grid = screen.getByTestId("review-columns");
    expect(grid.className).toContain("lg:grid-cols-[");
    expect(grid.children).toHaveLength(2);

    const [left, right] = Array.from(grid.children) as HTMLElement[];
    // The argument on the left: the business, the audit, the AI summary.
    expect(left.textContent).toContain("What the audit found");
    // The decision on the right, and it sticks while the evidence scrolls past it.
    expect(right.textContent).toContain("Opportunities");
    expect(right.className).toContain("lg:sticky");
    expect(screen.getByRole("button", { name: "Approve" }).closest("[data-testid='opportunity-card']")).not.toBeNull();
    expect(right.contains(screen.getByRole("button", { name: "Approve" }))).toBe(true);
  });
});
