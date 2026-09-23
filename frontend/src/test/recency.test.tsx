/**
 * The "Found within" control on the review queue and the leads list (spec v0.10.0 §2).
 *
 * Three things matter here, and the third is the one that is easy to get wrong: an empty
 * result under a narrow window is not "nothing to review" — it is "nothing was found
 * recently", and the page has to name the next action rather than show a blank table.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Leads } from "@/components/leads/Leads";
import { ReviewQueue } from "@/components/review/ReviewQueue";
import { RECENCY_OPTIONS } from "@/lib/api";
import { leadRead, me, queueItem, renderWithProviders, routeFetch } from "@/test/utils";

afterEach(() => vi.unstubAllGlobals());

const EMPTY = { items: [], next_cursor: null };

describe("the Found within control", () => {
  it("offers 24 hours, 3, 7 and 30 days, and defaults to any time", async () => {
    routeFetch({ "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } } });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));

    const select = screen.getByLabelText("Found within") as HTMLSelectElement;
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
      "Any time",
      "24 hours",
      "3 days",
      "7 days",
      "30 days",
    ]);
    expect(select.value).toBe("");
    // "1 day" is the same window as 24 hours, so only one of the two is offered.
    expect(RECENCY_OPTIONS.filter((option) => option.value === "1")).toHaveLength(1);
  });

  it("asks the queue for the window in days, and drops it again on Any time", async () => {
    const { calls } = routeFetch({
      "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } },
    });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));

    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "1" } });
    await waitFor(() => expect(calls.at(-1)?.url).toContain("discovered_within_days=1"));

    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "" } });
    await waitFor(() => expect(calls.at(-1)?.url).not.toContain("discovered_within_days"));
  });

  it("composes with the other filters in one request", async () => {
    const { calls } = routeFetch({ "GET /review-queue": { status: 200, body: EMPTY } });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));

    fireEvent.change(screen.getByLabelText("City"), { target: { value: "Austin" } });
    fireEvent.change(screen.getByLabelText("Service"), { target: { value: "website_design" } });
    fireEvent.change(screen.getByLabelText("Min score"), { target: { value: "0.5" } });
    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "7" } });

    await waitFor(() => {
      const url = calls.at(-1)?.url ?? "";
      expect(url).toContain("city=Austin");
      expect(url).toContain("service=website_design");
      expect(url).toContain("min_score=0.5");
      expect(url).toContain("discovered_within_days=7");
    });
  });

  it("keeps the window on the next page, so a cursor never widens it", async () => {
    const { calls } = routeFetch({
      "GET /review-queue": (_init, url) => ({
        status: 200,
        body: url.includes("cursor=")
          ? { items: [queueItem({ business_id: "biz-2", display_name: "Second" })], next_cursor: null }
          : { items: [queueItem()], next_cursor: "cursor-1" },
      }),
    });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));

    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "3" } });
    await waitFor(() => expect(calls.at(-1)?.url).toContain("discovered_within_days=3"));

    fireEvent.click(screen.getByRole("button", { name: /Load more/i }));

    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(2));
    const paged = calls.at(-1)?.url ?? "";
    expect(paged).toContain("cursor=cursor-1");
    expect(paged).toContain("discovered_within_days=3");
  });
});

describe("an empty result under a narrow window names the next action", () => {
  it("does not read as a blank table or as 'nothing to review'", async () => {
    routeFetch({ "GET /review-queue": { status: 200, body: EMPTY } });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getByText("Nothing to review.")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "1" } });

    await waitFor(() =>
      expect(screen.getByText("Nothing was first found in the last 24 hours.")).toBeTruthy(),
    );
    // Two ways forward, both named: widen the window, or go and find something new.
    expect(screen.getByRole("button", { name: "Show any time" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to Searches" })).toBeTruthy();
  });

  it("widens the window again from its own empty state", async () => {
    const { calls } = routeFetch({ "GET /review-queue": { status: 200, body: EMPTY } });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getByText("Nothing to review.")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "1" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Show any time" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "Show any time" }));

    await waitFor(() => expect(calls.at(-1)?.url).not.toContain("discovered_within_days"));
    expect((screen.getByLabelText("Found within") as HTMLSelectElement).value).toBe("");
  });
});

describe("the leads list has the same control", () => {
  it("asks for the window and shows the source column", async () => {
    const { calls } = routeFetch({
      "GET /leads": { status: 200, body: { items: [leadRead()], next_cursor: null } },
    });
    renderWithProviders(<Leads />, { user: me("sales_rep") });
    await waitFor(() => expect(screen.getAllByTestId("lead-row")).toHaveLength(1));

    expect(screen.getByTestId("source-cell").textContent).toContain("Google Places");

    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "30" } });
    await waitFor(() => expect(calls.at(-1)?.url).toContain("discovered_within_days=30"));
  });

  it("says what an empty narrow window means for a rep", async () => {
    routeFetch({ "GET /leads": { status: 200, body: EMPTY } });
    renderWithProviders(<Leads />, { user: me("sales_rep") });
    await waitFor(() => expect(screen.getByText("No leads yet.")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Found within"), { target: { value: "3" } });

    await waitFor(() =>
      expect(screen.getByText("No lead here was first found in the last 3 days.")).toBeTruthy(),
    );
    expect(screen.getByRole("button", { name: "Show any time" })).toBeTruthy();
  });
});
