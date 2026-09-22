import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "./Badge";

describe("Badge", () => {
  it("always carries its word, so colour is never the only signal", () => {
    render(<Badge tone="risk">Held</Badge>);
    expect(screen.getByText("Held").className).toContain("text-risk");
  });

  it("has one class set per tone", () => {
    const { container } = render(
      <>
        <Badge tone="ok">In CRM</Badge>
        <Badge tone="warn">Needs enrichment</Badge>
        <Badge tone="accent">Approved</Badge>
        <Badge>Pending</Badge>
      </>,
    );
    const tones = Array.from(container.querySelectorAll("span")).map((span) => span.className);
    expect(tones[0]).toContain("bg-ok-tint");
    expect(tones[1]).toContain("bg-warn-tint");
    expect(tones[2]).toContain("bg-accent-tint");
    expect(tones[3]).toContain("bg-surface-sunken");
  });
});
