import { useState } from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Tabs } from "./Tabs";

const OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "needs_enrichment", label: "Needs enrichment" },
] as const;

function Harness() {
  const [value, setValue] = useState<"pending" | "needs_enrichment">("pending");
  return (
    <>
      <Tabs label="Status" value={value} options={OPTIONS} onChange={setValue} panelId="queue" />
      <div id="queue" role="tabpanel" aria-labelledby={`tab-${value}`}>
        {value}
      </div>
    </>
  );
}

describe("Tabs", () => {
  it("marks the selected tab and links it to its panel", () => {
    render(<Harness />);
    const [pending, enrichment] = screen.getAllByRole("tab");
    expect(pending.getAttribute("aria-selected")).toBe("true");
    expect(enrichment.getAttribute("aria-selected")).toBe("false");
    expect(pending.getAttribute("aria-controls")).toBe("queue");
  });

  it("keeps only the selected tab in the tab order", () => {
    render(<Harness />);
    const [pending, enrichment] = screen.getAllByRole("tab");
    expect(pending.getAttribute("tabindex")).toBe("0");
    expect(enrichment.getAttribute("tabindex")).toBe("-1");
  });

  it("moves and selects with the arrow keys, wrapping round", () => {
    render(<Harness />);
    const list = screen.getByRole("tablist");
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(screen.getByRole("tabpanel").textContent).toBe("needs_enrichment");
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(screen.getByRole("tabpanel").textContent).toBe("pending");
    fireEvent.keyDown(list, { key: "End" });
    expect(screen.getByRole("tabpanel").textContent).toBe("needs_enrichment");
    fireEvent.keyDown(list, { key: "Home" });
    expect(screen.getByRole("tabpanel").textContent).toBe("pending");
  });

  it("selects on click", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("tab", { name: "Needs enrichment" }));
    expect(screen.getByRole("tabpanel").textContent).toBe("needs_enrichment");
  });
});
