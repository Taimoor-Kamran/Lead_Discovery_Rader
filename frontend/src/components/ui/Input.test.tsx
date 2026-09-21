import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Input } from "./Input";

describe("Input", () => {
  it("binds the label to the control, and the label alone is the accessible name", () => {
    render(<Input label="Email" hint="We only use it to sign you in" defaultValue="a@b.test" />);
    // `getByLabelText` with an exact string is what the e2e uses; a hint must not join it.
    const input = screen.getByLabelText("Email") as HTMLInputElement;
    expect(input.value).toBe("a@b.test");
    expect(input.getAttribute("aria-describedby")).toBe(input.id + "-hint");
  });

  it("marks an invalid field and points at its message", () => {
    render(<Input label="City" error="Give a city." />);
    const input = screen.getByLabelText("City");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBe(input.id + "-error");
    expect(screen.getByRole("alert").textContent).toBe("Give a city.");
  });

  it("passes native attributes through", () => {
    render(<Input label="Min score" type="number" min={0} max={1} required />);
    const input = screen.getByLabelText("Min score") as HTMLInputElement;
    expect(input.type).toBe("number");
    expect(input.required).toBe(true);
    expect(input.min).toBe("0");
  });
});
