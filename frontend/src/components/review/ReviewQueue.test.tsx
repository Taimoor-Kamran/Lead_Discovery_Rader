import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { ReviewQueue } from "./ReviewQueue";
import { me, queueItem, renderWithProviders, routeFetch } from "@/test/utils";

afterEach(() => vi.unstubAllGlobals());

describe("ReviewQueue page", () => {
  it("hides weak signals by default and asks for them when the toggle is on", async () => {
    const { calls } = routeFetch({
      "GET /review-queue": (_init, url) => ({
        status: 200,
        body: {
          items: url.includes("include_weak=true")
            ? [queueItem({ weak_hidden: 0, opportunities: [...queueItem().opportunities, { ...queueItem().opportunities[0], id: "opp-weak", service: "ads_social", service_name: "Ads", confidence: 0.3, weak: true }] })]
            : [queueItem()],
          next_cursor: null,
        },
      }),
    });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });

    const queueCalls = () => calls.filter((call) => call.url.includes("/review-queue"));
    await waitFor(() => expect(screen.getByTestId("weak-hidden")).toBeTruthy());
    expect(queueCalls()[0].url).toContain("include_weak=false");
    const chips = () => screen.getAllByTestId("service-chip").map((chip) => chip.textContent ?? "");
    expect(chips().some((text) => text.includes("Ads & social"))).toBe(false);
    // Selection boxes stay out of the way until the row is hovered…
    expect(screen.getByLabelText("Select Website redesign for Barton Creek Plumbing").className).toContain("group-hover:opacity-100");

    fireEvent.click(screen.getByLabelText("Show weak signals"));

    await waitFor(() => expect(chips().some((text) => text.includes("Ads & social"))).toBe(true));
    expect(queueCalls().at(-1)?.url).toContain("include_weak=true");
    expect(screen.queryByTestId("weak-hidden")).toBeNull();
    // …and are always shown once weak signals are on, because that is batch-triage mode.
    expect(screen.getByLabelText("Select Ads & social for Barton Creek Plumbing").className).not.toContain("opacity-0");
  });

  it("sends the sort, direction, state, industry and listing filters to the API", async () => {
    const { calls } = routeFetch({
      "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } },
      "GET /search-jobs/industries": { status: 200, body: [{ key: "plumbing", label: "Plumbing", query: "plumber" }] },
    });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    const queueCalls = () => calls.filter((call) => call.url.includes("/review-queue"));
    await waitFor(() => expect(queueCalls().length).toBe(1));
    expect(queueCalls()[0].url).toContain("sort=score");
    expect(queueCalls()[0].url).toContain("sort_dir=desc");
    await screen.findByRole("option", { name: "Plumbing" });

    fireEvent.click(screen.getByRole("button", { name: /Reviews/ }));
    await waitFor(() => expect(queueCalls().at(-1)?.url).toContain("sort=reviews"));
    expect(queueCalls().at(-1)?.url).toContain("sort_dir=desc");
    fireEvent.click(screen.getByRole("button", { name: /Reviews/ }));
    await waitFor(() => expect(queueCalls().at(-1)?.url).toContain("sort_dir=asc"));

    fireEvent.change(screen.getByLabelText("State"), { target: { value: "tx" } });
    fireEvent.change(screen.getByLabelText("Industry"), { target: { value: "plumbing" } });
    fireEvent.change(screen.getByLabelText("Listing"), { target: { value: "no_website" } });
    await waitFor(() => expect(queueCalls().at(-1)?.url).toContain("badge=no_website"));
    const last = queueCalls().at(-1)?.url ?? "";
    expect(last).toContain("state=tx");
    expect(last).toContain("industry=plumbing");
    expect(last).toContain("sort=reviews");
  });

  it("offers batch reject and not-a-fit only, and only with a selection", async () => {
    routeFetch({ "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } } });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));

    const bar = screen.getByTestId("batch-bar");
    const buttons = Array.from(bar.querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons).toEqual(["Reject selected", "Not a fit selected"]);
    expect(screen.getByRole("button", { name: "Reject selected" })).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByLabelText("Select Website redesign for Barton Creek Plumbing"));
    expect(screen.getByRole("button", { name: "Reject selected" })).toHaveProperty("disabled", false);
    fireEvent.click(screen.getByRole("button", { name: "Reject selected" }));
    expect(screen.getByRole("dialog").textContent).toContain("Reject");
    expect(screen.getByLabelText("Reason")).toBeTruthy();
  });

  it("gives a read-only role no selection and no batch bar", async () => {
    routeFetch({ "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } } });
    renderWithProviders(<ReviewQueue />, { user: me("crm_manager") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));
    expect(screen.queryByTestId("batch-bar")).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /Select Website/ })).toBeNull();
  });
});
