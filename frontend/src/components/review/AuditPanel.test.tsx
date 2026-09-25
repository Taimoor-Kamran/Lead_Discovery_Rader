import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AuditPanel } from "./AuditPanel";
import { reviewDetail } from "@/test/utils";

const AUDIT = {
  id: "audit-1",
  business_id: "biz-1",
  job_run_id: null,
  url_audited: "https://bartoncreekplumbing.invalid/",
  final_url: "https://bartoncreekplumbing.invalid/",
  status: "done" as const,
  http_status: 200,
  finding_codes: ["no_contact_on_homepage", "slow_mobile"],
  rules_version: "audit-1",
  started_at: null,
  finished_at: null,
  created_at: "2026-09-20T10:00:00Z",
  checks: {},
  psi: { performance_score: 55, lcp_ms: 3600, cls: 0.11, tbt_ms: null, crux_category: null, strategy: "mobile" },
  tech_stack: { platforms: ["wordpress"] },
  findings: [
    {
      code: "no_contact_on_homepage",
      severity: "high",
      message: "Audit found no phone link, email link or contact form on the homepage.",
      evidence_text: "<script>alert(1)</script>",
      evidence_url: "javascript:alert(1)",
    },
    { code: "slow_mobile", severity: "medium", message: "PageSpeed Insights scored the homepage 55 out of 100 on mobile.", evidence_text: null, evidence_url: null },
  ],
  page_text: null,
  page_text_hidden: true,
  html_sha256: null,
  content_expires_at: null,
  purged_at: null,
};

describe("AuditPanel", () => {
  it("names findings in plain words with the code in a tooltip, and renders evidence safely", () => {
    const { container } = render(<AuditPanel detail={reviewDetail({ audit: AUDIT })} />);
    const findings = screen.getAllByTestId("finding");
    expect(findings[0].textContent).toContain("No contact info on homepage");
    expect(findings[0].textContent).not.toContain("no_contact_on_homepage");
    expect(screen.getByTitle("no_contact_on_homepage")).toBeTruthy();
    expect(findings[0].textContent).toContain("High");
    expect(findings[1].textContent).toContain("Slow on mobile");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
    expect(findings[0].textContent).toContain("<script>alert(1)</script>");
    expect(screen.getByTitle("done · audit-1").textContent).toContain("Audited");
  });

  it("shows PageSpeed as labelled numbers with a good / needs work / poor band", () => {
    render(<AuditPanel detail={reviewDetail({ audit: AUDIT })} />);
    const lines = screen.getAllByTestId("psi-line").map((line) => line.textContent);
    expect(lines).toEqual([
      "Mobile score55/100needs work",
      "Load time (LCP)3.6 sneeds work",
      "Layout shift (CLS)0.11needs work",
      "Blocking time (TBT)unknown",
    ]);
    expect(screen.getByTestId("psi").textContent).not.toContain("lcp_ms");
  });

  it("says so when there is no audit", () => {
    render(<AuditPanel detail={reviewDetail({ audit: null })} />);
    expect(screen.getByText("This business has not been audited yet.")).toBeTruthy();
  });

  it("names the block Speed and quality and adds the scores PageSpeed gave, never a zero", () => {
    const scored = { ...AUDIT, psi: { ...AUDIT.psi, accessibility_score: 58, best_practices_score: 67 } };
    const { unmount } = render(<AuditPanel detail={reviewDetail({ audit: scored })} />);
    expect(screen.getByText("Speed and quality")).toBeTruthy();
    const lines = screen.getAllByTestId("psi-line").map((line) => line.textContent);
    expect(lines).toContain("Accessibility58/100needs work");
    expect(lines).toContain("Best practices67/100needs work");
    unmount();
    render(<AuditPanel detail={reviewDetail({ audit: { ...AUDIT, psi: null } })} />);
    expect(screen.getByText("No PageSpeed measurement.")).toBeTruthy();
    expect(screen.queryByText(/0\/100/)).toBeNull();
  });
});
