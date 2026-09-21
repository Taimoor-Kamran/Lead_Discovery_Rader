import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

function open(onClose = vi.fn()) {
  render(
    <Dialog title="Reject" onClose={onClose} footer={<Button onClick={onClose}>Cancel</Button>}>
      <input aria-label="Note" />
    </Dialog>,
  );
  return onClose;
}

/** The real shape: a trigger that opens the dialog and should get focus back after it. */
function Harness() {
  const [showing, setShowing] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setShowing(true)}>
        Reject
      </button>
      {showing ? (
        <Dialog title="Reject" onClose={() => setShowing(false)}>
          <input aria-label="Note" />
        </Dialog>
      ) : null}
    </>
  );
}

describe("Dialog", () => {
  it("is a modal named by its heading", () => {
    open();
    expect(screen.getByRole("dialog", { name: "Reject" }).getAttribute("aria-modal")).toBe("true");
  });

  it("moves focus to the first control inside it", () => {
    open();
    expect(document.activeElement).toBe(screen.getByLabelText("Note"));
  });

  it("closes on Esc", () => {
    const onClose = open();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("traps Tab inside itself", () => {
    open();
    const note = screen.getByLabelText("Note");
    const cancel = screen.getByRole("button", { name: "Cancel" });
    cancel.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(note);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(cancel);
  });

  it("gives focus back to whatever opened it", () => {
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Reject" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(document.activeElement).toBe(screen.getByLabelText("Note"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("can be the form that submits it", () => {
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(
      <Dialog as="form" title="Approve" onClose={() => {}} onSubmit={onSubmit}>
        <button type="submit">Approve</button>
      </Dialog>,
    );
    fireEvent.submit(screen.getByRole("dialog", { name: "Approve" }));
    expect(onSubmit).toHaveBeenCalled();
  });
});
