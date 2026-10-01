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

  it("shows the listing's review count as a chip, and no chip when the listing gave none", () => {
    render(
      <QueueTable
        items={[
          queueItem({ rating: 4.1, user_rating_count: 11 }),
          queueItem({ business_id: "biz-2", display_name: "Oak Hill", rating: null, user_rating_count: null }),
        ]}
        selected={new Set()}
        onToggle={() => {}}
        onToggleBusiness={() => {}}
        canSelect={false}
      />,
    );
    const chips = screen.getAllByTestId("review-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toBe("11");
    expect(chips[0].getAttribute("title")).toBe("Rated 4.1 on the business listing");
    const reviews = screen.getAllByTestId("reviews").map((cell) => cell.textContent);
    expect(reviews).toEqual(["11★ 4.1", "—"]);
  });

  it("shows PageSpeed, finding count and website, and '—' — never 0 — for what was not measured", () => {
    render(
      <QueueTable
        items={[
          queueItem(),
          queueItem({
            business_id: "biz-2",
            display_name: "Never Read",
            latest_audit: {
              status: "unreachable",
              audited_at: "2026-09-20T10:00:00Z",
              top_findings: [],
              finding_count: null,
              pagespeed_score: null,
            },
            website_kind: "social_profile",
          }),
          queueItem({ business_id: "biz-3", display_name: "Not Audited", latest_audit: null }),
        ]}
        selected={new Set()}
        onToggle={() => {}}
        onToggleBusiness={() => {}}
        canSelect={false}
      />,
    );
    const text = (id: string) => screen.getAllByTestId(id).map((cell) => cell.textContent);
    expect(text("pagespeed")).toEqual(["62", "—", "—"]);
    expect(text("finding-count")).toEqual(["4", "—", "—"]);
    expect(text("website-kind")).toEqual(["Own website", "Social profile only", "Own website"]);
    for (const cell of screen.getAllByTestId("pagespeed").slice(1)) {
      expect(cell.textContent).not.toContain("0");
      expect(cell.querySelector("[title]")?.getAttribute("title")).toBe("PageSpeed was not measured for this site");
    }
    expect(screen.getAllByTestId("finding-count")[1].querySelector("[title]")?.getAttribute("title")).toBe(
      "The audit did not read the page",
    );
  });

  it("tells two rows with the same score apart by their finding count", () => {
    render(
      <QueueTable
        items={[
          queueItem({ top_score: 0.7 }),
          queueItem({
            business_id: "biz-2",
            display_name: "Oak Hill",
            top_score: 0.7,
            latest_audit: { ...queueItem().latest_audit!, finding_count: 9 },
          }),
        ]}
        selected={new Set()}
        onToggle={() => {}}
        onToggleBusiness={() => {}}
        canSelect={false}
      />,
    );
    const [first, second] = screen.getAllByTestId("queue-row");
    expect(first.querySelector("td:last-child")?.textContent).toBe(second.querySelector("td:last-child")?.textContent);
    expect(first.textContent).not.toBe(second.textContent?.replace("Oak Hill", "Barton Creek Plumbing"));
    expect(screen.getAllByTestId("finding-count").map((cell) => cell.textContent)).toEqual(["4", "9"]);
  });

  it("badges a no-website and a closed listing, differently from each other and from a normal row", () => {
    render(
      <QueueTable
        items={[
          queueItem(),
          queueItem({ business_id: "biz-2", display_name: "No Site", website: null, website_kind: "none", badges: ["no_website"] }),
          queueItem({
            business_id: "biz-3",
            display_name: "Gone",
            business_status: "closed_permanently",
            badges: ["closed_permanently"],
          }),
        ]}
        selected={new Set()}
        onToggle={() => {}}
        onToggleBusiness={() => {}}
        canSelect={false}
      />,
    );
    const rows = screen.getAllByTestId("queue-row");
    expect(rows[0].querySelector("[data-testid=data-badge]")).toBeNull();
    const badges = screen.getAllByTestId("data-badge");
    expect(badges.map((badge) => badge.textContent)).toEqual(["No website", "Permanently closed"]);
    expect(badges[0].className).not.toBe(badges[1].className);
    expect(rows[1].contains(badges[0])).toBe(true);
    expect(rows[2].contains(badges[1])).toBe(true);
  });

  it("sorts on the Reviews and Score headers and marks the active one", () => {
    const onSort = vi.fn();
    render(
      <QueueTable
        items={[queueItem()]}
        selected={new Set()}
        onToggle={() => {}}
        onToggleBusiness={() => {}}
        canSelect={false}
        sort="reviews"
        sortDir="asc"
        onSort={onSort}
      />,
    );
    const reviews = screen.getByRole("columnheader", { name: /Reviews/ });
    const scoreHeader = screen.getByRole("columnheader", { name: /Score/ });
    expect(reviews.getAttribute("aria-sort")).toBe("ascending");
    expect(scoreHeader.getAttribute("aria-sort")).toBe("none");
    fireEvent.click(screen.getByRole("button", { name: /Score/ }));
    expect(onSort).toHaveBeenCalledWith("score");
    expect(screen.getByRole("columnheader", { name: /PageSpeed/ }).getAttribute("aria-sort")).toBeNull();
  });
});
