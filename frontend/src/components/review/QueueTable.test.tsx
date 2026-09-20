import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueueTable } from "./QueueTable";
import { queueItem } from "@/test/utils";

describe("QueueTable", () => {
  it("shows city and state next to the business name, and the hidden-weak count", () => {
    render(
      <QueueTable
        items={[queueItem(), queueItem({ business_id: "biz-2", display_name: "Oak Hill", city: "Round Rock", state: "TX", weak_hidden: 0 })]}
        selected={new Set()}
        onToggle={() => {}}
        onToggleBusiness={() => {}}
        canSelect
      />,
    );
    const rows = screen.getAllByTestId("queue-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Barton Creek Plumbing");
    expect(screen.getAllByTestId("queue-place").map((el) => el.textContent)).toEqual([
      "Austin, TX",
      "Round Rock, TX",
    ]);
    expect(screen.getByTestId("weak-hidden").textContent).toBe("1 weak signal hidden");
    // Human wording on the page, the raw code in the tooltip only.
    expect(rows[0].textContent).toContain("No HTTPS");
    expect(rows[0].textContent).not.toContain("no_https");
    expect(screen.getAllByTitle("no_https").length).toBeGreaterThan(0);
    expect(rows[0].textContent).toContain("Website redesign");
    expect(rows[0].textContent).not.toContain("website_design");
    expect(rows[0].textContent).toContain("Audited");
  });

  it("shows one clear number per chip and keeps confidence in the tooltip; the score column is 0–100", () => {
    render(
      <QueueTable items={[queueItem()]} selected={new Set()} onToggle={() => {}} onToggleBusiness={() => {}} canSelect={false} />,
    );
    const chip = screen.getByTestId("service-chip");
    expect(chip.textContent).toBe("Website redesignScore 72");
    expect(chip.textContent).not.toContain("%");
    expect(chip.getAttribute("title")).toContain("confidence 80%");
    expect(chip.getAttribute("title")).toContain("website_design");
    const row = screen.getByTestId("queue-row");
    expect(row.querySelector("td:last-child")?.textContent).toBe("72");
  });

  it("hides the checkboxes until hover unless weak signals are shown", () => {
    const { unmount } = render(
      <QueueTable items={[queueItem()]} selected={new Set()} onToggle={() => {}} onToggleBusiness={() => {}} canSelect />,
    );
    for (const box of screen.getAllByRole("checkbox")) expect(box.className).toContain("group-hover:opacity-100");
    unmount();
    render(
      <QueueTable items={[queueItem()]} selected={new Set()} onToggle={() => {}} onToggleBusiness={() => {}} canSelect showWeak />,
    );
    for (const box of screen.getAllByRole("checkbox")) expect(box.className).not.toContain("opacity-0");
  });

  it("says 'location unknown' rather than inventing one", () => {
    render(
      <QueueTable
        items={[queueItem({ city: null, state: null })]}
        selected={new Set()}
        onToggle={() => {}}
        onToggleBusiness={() => {}}
        canSelect={false}
      />,
    );
    expect(screen.getByTestId("queue-place").textContent).toBe("location unknown");
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("toggles an opportunity and a whole business", () => {
    const onToggle = vi.fn();
    const onToggleBusiness = vi.fn();
    render(
      <QueueTable items={[queueItem()]} selected={new Set()} onToggle={onToggle} onToggleBusiness={onToggleBusiness} canSelect />,
    );
    fireEvent.click(screen.getByLabelText("Select Website redesign for Barton Creek Plumbing"));
    expect(onToggle).toHaveBeenCalledWith("opp-1");
    fireEvent.click(screen.getByLabelText("Select every opportunity of Barton Creek Plumbing"));
    expect(onToggleBusiness).toHaveBeenCalled();
  });
});
