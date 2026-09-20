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
    expect(rows[0].textContent).toContain("no_https");
    expect(rows[0].textContent).toContain("website_design");
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
    fireEvent.click(screen.getByLabelText("Select Website design / redesign for Barton Creek Plumbing"));
    expect(onToggle).toHaveBeenCalledWith("opp-1");
    fireEvent.click(screen.getByLabelText("Select every opportunity of Barton Creek Plumbing"));
    expect(onToggleBusiness).toHaveBeenCalled();
  });
});
