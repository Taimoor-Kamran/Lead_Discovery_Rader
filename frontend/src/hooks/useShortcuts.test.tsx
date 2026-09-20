import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { useShortcuts } from "./useShortcuts";

function Harness({ onA, enabled = true }: { onA: () => void; enabled?: boolean }) {
  useShortcuts({ a: onA }, enabled);
  return (
    <div>
      <input aria-label="Note" />
      <textarea aria-label="Long note" />
      <button type="button">Plain</button>
    </div>
  );
}

describe("useShortcuts", () => {
  it("fires on the page but never while typing in an input or textarea", () => {
    const onA = vi.fn();
    const { getByLabelText, getByText } = render(<Harness onA={onA} />);

    fireEvent.keyDown(getByText("Plain"), { key: "a" });
    expect(onA).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(getByLabelText("Note"), { key: "a" });
    fireEvent.keyDown(getByLabelText("Long note"), { key: "a" });
    expect(onA).toHaveBeenCalledTimes(1);
  });

  it("ignores modifier chords and is silent when disabled", () => {
    const onA = vi.fn();
    const { getByText, rerender } = render(<Harness onA={onA} />);
    fireEvent.keyDown(getByText("Plain"), { key: "a", ctrlKey: true });
    expect(onA).not.toHaveBeenCalled();
    rerender(<Harness onA={onA} enabled={false} />);
    fireEvent.keyDown(getByText("Plain"), { key: "a" });
    expect(onA).not.toHaveBeenCalled();
  });
});
