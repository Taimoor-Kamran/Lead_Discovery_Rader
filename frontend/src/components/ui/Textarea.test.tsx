import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Textarea } from "./Input";

describe("Textarea", () => {
  it("is labelled, sized and reports what was typed", () => {
    const onChange = vi.fn();
    render(<Textarea label="Note" rows={3} onChange={onChange} />);
    const field = screen.getByLabelText("Note") as HTMLTextAreaElement;
    expect(field.rows).toBe(3);
    fireEvent.change(field, { target: { value: "Owner asked" } });
    expect(onChange).toHaveBeenCalled();
  });

  it("shows a required note's error", () => {
    render(<Textarea label="Note" error="A note is required." />);
    expect(screen.getByLabelText("Note").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("alert").textContent).toBe("A note is required.");
  });
});
