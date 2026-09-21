import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { SeverityDot } from "./SeverityDot";

describe("SeverityDot", () => {
  it("is decorative: the severity word next to it carries the meaning", () => {
    const { container } = render(<SeverityDot severity="high" />);
    const dot = container.querySelector("span")!;
    expect(dot.getAttribute("aria-hidden")).toBe("true");
    expect(dot.className).toContain("bg-risk");
  });

  it("falls back to the quietest tone for an unknown severity", () => {
    const { container } = render(<SeverityDot severity="something-new" />);
    expect(container.querySelector("span")!.className).toContain("bg-line");
  });

  it("maps medium and low to their own tones", () => {
    const { container } = render(
      <>
        <SeverityDot severity="medium" />
        <SeverityDot severity="low" />
      </>,
    );
    const dots = Array.from(container.querySelectorAll("span")).map((s) => s.className);
    expect(dots[0]).toContain("bg-warn");
    expect(dots[1]).toContain("bg-ink-soft");
  });
});
