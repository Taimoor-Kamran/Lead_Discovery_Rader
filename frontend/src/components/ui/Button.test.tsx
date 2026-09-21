import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Button } from "./Button";

describe("Button", () => {
  it("is a non-submitting button unless it says otherwise", () => {
    const { rerender } = render(<Button>Approve</Button>);
    expect(screen.getByRole("button", { name: "Approve" }).getAttribute("type")).toBe("button");
    rerender(<Button type="submit">Approve</Button>);
    expect(screen.getByRole("button", { name: "Approve" }).getAttribute("type")).toBe("submit");
  });

  it("carries each variant and size without naming a raw colour", () => {
    render(
      <>
        <Button variant="primary">Primary</Button>
        <Button variant="danger" size="sm">
          Danger
        </Button>
        <Button variant="ghost">Ghost</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Primary" }).className).toContain("bg-accent");
    expect(screen.getByRole("button", { name: "Danger" }).className).toContain("text-risk");
    expect(screen.getByRole("button", { name: "Danger" }).className).toContain("text-sm");
    expect(screen.getByRole("button", { name: "Ghost" }).className).toContain("bg-transparent");
  });

  it("while loading is busy, disabled and may change the word", () => {
    const onClick = vi.fn();
    render(
      <Button loading loadingLabel="Signing in…" onClick={onClick}>
        Sign in
      </Button>,
    );
    const button = screen.getByRole("button", { name: /Signing in/ });
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("does not fire while disabled", () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Reject
      </Button>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
