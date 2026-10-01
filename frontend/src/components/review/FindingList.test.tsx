import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FindingList } from "./FindingList";

describe("FindingList (v0.14.0, F8)", () => {
  it("says how each finding was established, and nothing when an older audit does not say", () => {
    render(
      <FindingList
        findings={[
          { code: "no_https", severity: "high", method: "deterministic", evidence_text: "served over http" },
          { code: "slow_mobile", severity: "medium", method: "api", evidence_text: "mobile performance 31/100" },
          { code: "no_h1", severity: "low" },
        ]}
      />,
    );
    const methods = screen.getAllByTestId("finding-method");
    expect(methods.map((m) => m.textContent)).toEqual(["Our check", "Outside service"]);
    expect(methods[1].getAttribute("title")).toContain("outside service");
    const rows = screen.getAllByTestId("finding");
    expect(rows[2].querySelector("[data-testid=finding-method]")).toBeNull();
    expect(rows[1].textContent).toContain("mobile performance 31/100");
  });

  it("marks a finding filed under no service as context, and an older one without the key as neither", () => {
    render(
      <FindingList
        findings={[
          { code: "no_https", severity: "high", service: "website_design" },
          { code: "no_dmarc", severity: "low", service: null },
          { code: "no_h1", severity: "low" },
        ]}
      />,
    );
    const rows = screen.getAllByTestId("finding");
    expect(rows.map((r) => r.dataset.context ?? "")).toEqual(["", "true", ""]);
    const badges = screen.getAllByTestId("finding-context");
    expect(badges).toHaveLength(1);
    expect(badges[0].textContent).toBe("Context");
    expect(rows[1].contains(badges[0])).toBe(true);
  });
});
