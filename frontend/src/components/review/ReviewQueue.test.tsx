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

    await waitFor(() => expect(screen.getByTestId("weak-hidden")).toBeTruthy());
    expect(calls[0].url).toContain("include_weak=false");
    expect(screen.queryByText("ads_social")).toBeNull();

    fireEvent.click(screen.getByLabelText("Show weak signals"));

    await waitFor(() => expect(screen.getByText("ads_social")).toBeTruthy());
    expect(calls.at(-1)?.url).toContain("include_weak=true");
    expect(screen.queryByTestId("weak-hidden")).toBeNull();
  });

  it("offers batch reject and not-a-fit only, and only with a selection", async () => {
    routeFetch({ "GET /review-queue": { status: 200, body: { items: [queueItem()], next_cursor: null } } });
    renderWithProviders(<ReviewQueue />, { user: me("reviewer") });
    await waitFor(() => expect(screen.getAllByTestId("queue-row")).toHaveLength(1));

    const bar = screen.getByTestId("batch-bar");
    const buttons = Array.from(bar.querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons).toEqual(["Reject selected", "Not a fit selected"]);
    expect(screen.getByRole("button", { name: "Reject selected" })).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByLabelText("Select Website design / redesign for Barton Creek Plumbing"));
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
