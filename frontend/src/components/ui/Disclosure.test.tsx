import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Disclosure } from "./Disclosure";

describe("Disclosure", () => {
  it("starts closed, says so, and controls the region it hides", () => {
    render(
      <Disclosure summary={(open) => (open ? "Hide details" : "Show details")}>
        <p>Model: fake</p>
      </Disclosure>,
    );
    const trigger = screen.getByRole("button", { name: "Show details" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Model: fake")).toBeNull();
    expect(document.getElementById(trigger.getAttribute("aria-controls")!)).toBeTruthy();
  });

  it("opens and closes on click", () => {
    render(<Disclosure summary="Show details">Provenance</Disclosure>);
    const trigger = screen.getByRole("button");
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Provenance")).toBeTruthy();
    fireEvent.click(trigger);
    expect(screen.queryByText("Provenance")).toBeNull();
  });

  it("can start open", () => {
    render(
      <Disclosure summary="Details" defaultOpen>
        Already here
      </Disclosure>,
    );
    expect(screen.getByText("Already here")).toBeTruthy();
  });
});
