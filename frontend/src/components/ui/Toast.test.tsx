import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ToastProvider, useToast } from "./Toast";

function Trigger({ onUndo }: { onUndo?: () => void }) {
  const { show } = useToast();
  return (
    <button
      type="button"
      onClick={() =>
        show({
          tone: "success",
          message: "Website redesign approved.",
          action: onUndo ? { label: "Undo", run: onUndo } : undefined,
        })
      }
    >
      Approve
    </button>
  );
}

function renderToast(onUndo?: () => void) {
  render(
    <ToastProvider>
      <Trigger onUndo={onUndo} />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
}

describe("Toast", () => {
  it("announces what happened in a polite live region", () => {
    renderToast();
    expect(screen.getByRole("status").textContent).toContain("Website redesign approved.");
  });

  it("runs its action and then takes itself away", async () => {
    const onUndo = vi.fn();
    renderToast(onUndo);
    // The action may be async, and dismissing the toast afterwards is a state update.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    });
    expect(onUndo).toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("can be dismissed", () => {
    renderToast();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("refuses to be used outside its provider", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Trigger />)).toThrow(/ToastProvider/);
    quiet.mockRestore();
  });
});
