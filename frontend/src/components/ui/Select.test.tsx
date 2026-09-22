import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Select } from "./Input";

/** A harness so the test exercises the control the way a screen uses it: controlled. */
function ServicePicker({ onPick }: { onPick: (value: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <Select
      label="Service"
      value={value}
      onChange={(event) => {
        setValue(event.target.value);
        onPick(event.target.value);
      }}
    >
      <option value="">Any service</option>
      <option value="website_design">Website redesign</option>
      <option value="seo_gbp">SEO / Google profile</option>
    </Select>
  );
}

describe("Select", () => {
  it("is a labelled combobox that reports its choice", () => {
    const onPick = vi.fn();
    render(<ServicePicker onPick={onPick} />);
    const select = screen.getByRole("combobox", { name: "Service" }) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "seo_gbp" } });
    expect(onPick).toHaveBeenCalledWith("seo_gbp");
    expect(select.value).toBe("seo_gbp");
  });

  it("carries a hint without letting it into the accessible name", () => {
    render(
      <Select label="Sales rep" hint="Only reps with an account appear here">
        <option value="">Anyone</option>
      </Select>,
    );
    const select = screen.getByRole("combobox", { name: "Sales rep" });
    expect(select.getAttribute("aria-describedby")).toBe(select.id + "-hint");
  });
});
