import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DecisionDialog } from "./DecisionDialog";

function open(decision: Parameters<typeof DecisionDialog>[0]["decision"]) {
  const onSubmit = vi.fn();
  render(<DecisionDialog decision={decision} onSubmit={onSubmit} onCancel={() => {}} assignees={[{ id: "rep-1", email: "rep1@example.com" }]} />);
  return onSubmit;
}

describe("DecisionDialog", () => {
  it("reject needs a reason, and a note when the reason is other", () => {
    const onSubmit = open("reject");
    fireEvent.submit(screen.getByRole("dialog"));
    expect(screen.getByRole("alert").textContent).toBe("Pick a reason.");
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "other" } });
    fireEvent.submit(screen.getByRole("dialog"));
    expect(screen.getByRole("alert").textContent).toBe("A note is required.");
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/Note/), { target: { value: "Site is fine" } });
    fireEvent.submit(screen.getByRole("dialog"));
    expect(onSubmit).toHaveBeenCalledWith({ reason_code: "other", note: "Site is fine" });
  });

  it("not a fit has its own reasons", () => {
    open("not_a_fit");
    const options = Array.from(screen.getByLabelText("Reason").querySelectorAll("option")).map((o) => o.value);
    expect(options).toEqual(["", "too_small", "too_large", "outside_area", "already_client", "other"]);
  });

  it("needs enrichment needs a note", () => {
    const onSubmit = open("needs_enrichment");
    fireEvent.submit(screen.getByRole("dialog"));
    expect(screen.getByRole("alert").textContent).toBe("A note is required.");
    fireEvent.change(screen.getByLabelText(/Note/), { target: { value: "Check hours" } });
    fireEvent.submit(screen.getByRole("dialog"));
    expect(onSubmit).toHaveBeenCalledWith({ note: "Check hours" });
  });

  it("do not contact needs a note and an explicit confirmation", () => {
    const onSubmit = open("do_not_contact");
    expect(screen.getByRole("dialog").textContent).toContain("every");
    expect(screen.getByRole("button", { name: "Confirm: do not contact" })).toBeTruthy();
    fireEvent.submit(screen.getByRole("dialog"));
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Note/), { target: { value: "Owner asked" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm: do not contact" }));
    expect(onSubmit).toHaveBeenCalledWith({ note: "Owner asked" });
  });

  it("approve takes an optional note and an optional rep", () => {
    const onSubmit = open("approve");
    fireEvent.submit(screen.getByRole("dialog"));
    expect(onSubmit).toHaveBeenCalledWith({});
    fireEvent.change(screen.getByLabelText(/Assign to sales rep/), { target: { value: "rep-1" } });
    fireEvent.submit(screen.getByRole("dialog"));
    expect(onSubmit).toHaveBeenLastCalledWith({ assigned_to: "rep-1" });
  });

  it("duplicate needs a target", () => {
    const onSubmit = vi.fn();
    render(<DecisionDialog decision="duplicate" service="website_design" onSubmit={onSubmit} onCancel={() => {}} />);
    fireEvent.submit(screen.getByRole("dialog"));
    expect(screen.getByRole("alert").textContent).toBe("Pick the opportunity this duplicates.");
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
