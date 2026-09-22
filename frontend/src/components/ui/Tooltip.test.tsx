import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Tooltip } from "./Tooltip";

describe("Tooltip", () => {
  it("describes its target and stays hidden until asked for", () => {
    render(
      <Tooltip content="Facts, inference, intent, contactability">
        <button type="button">Score</button>
      </Tooltip>,
    );
    const tooltip = screen.getByRole("tooltip", { hidden: true });
    expect(tooltip.hasAttribute("hidden")).toBe(true);
    const described = screen.getByRole("button", { name: "Score" }).parentElement!;
    expect(described.getAttribute("aria-describedby")).toBe(tooltip.id);
  });

  it("opens on hover and on focus, and Esc dismisses it", () => {
    render(
      <Tooltip content="Raw score 0.72">
        <button type="button">72</button>
      </Tooltip>,
    );
    const target = screen.getByRole("button", { name: "72" });
    const wrapper = target.parentElement!.parentElement!;
    fireEvent.mouseEnter(wrapper);
    expect(screen.getByRole("tooltip").hasAttribute("hidden")).toBe(false);
    fireEvent.mouseLeave(wrapper);
    expect(screen.getByRole("tooltip", { hidden: true }).hasAttribute("hidden")).toBe(true);
    fireEvent.focus(wrapper);
    expect(screen.getByRole("tooltip").hasAttribute("hidden")).toBe(false);
    fireEvent.keyDown(wrapper, { key: "Escape" });
    expect(screen.getByRole("tooltip", { hidden: true }).hasAttribute("hidden")).toBe(true);
  });
});
