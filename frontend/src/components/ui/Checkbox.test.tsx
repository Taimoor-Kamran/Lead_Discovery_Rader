import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Checkbox } from "./Input";

describe("Checkbox", () => {
  it("toggles through its label", () => {
    const onChange = vi.fn();
    render(<Checkbox label="Show weak signals" checked={false} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Show weak signals"));
    expect(onChange).toHaveBeenCalled();
  });

  it("reflects the checked state it is given", () => {
    render(<Checkbox label="Active only" checked readOnly />);
    expect((screen.getByLabelText("Active only") as HTMLInputElement).checked).toBe(true);
  });
});
