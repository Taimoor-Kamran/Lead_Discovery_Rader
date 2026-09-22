import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Chip } from "./Chip";

describe("Chip", () => {
  it("is a quiet span by default", () => {
    render(<Chip>Website redesign</Chip>);
    const chip = screen.getByText("Website redesign");
    expect(chip.tagName).toBe("SPAN");
    expect(chip.className).toContain("border-line");
    expect(chip.className).not.toContain("cursor-pointer");
  });

  it("can become the label of a control and show it is interactive", () => {
    render(
      <Chip as="label" interactive>
        Pick me
      </Chip>,
    );
    const chip = screen.getByText("Pick me");
    expect(chip.tagName).toBe("LABEL");
    expect(chip.className).toContain("cursor-pointer");
  });
});
