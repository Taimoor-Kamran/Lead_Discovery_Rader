import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { BusinessReview, CONFLICT_MESSAGE, QUEUE_ORDER_KEY } from "./BusinessReview";
import { envelope, me, renderWithProviders, reviewDetail, reviewOpportunity, routeFetch, router } from "@/test/utils";

const REPS = { status: 200, body: { items: [me("sales_rep", "rep1@example.com")], next_cursor: null } };

function decided(overrides = {}) {
  return {
    ...reviewOpportunity({ review_status: "approved", lock_version: 1 }),
    decision: {
      id: "dec-1",
      opportunity_id: "opp-1",
      decision: "approve",
      from_status: "pending",
      to_status: "approved",
      reason_code: null,
      note: null,
      duplicate_of: null,
      assigned_to: null,
      assigned_to_email: null,
      decided_by: "user-reviewer",
      decided_by_email: "reviewer@example.com",
      decided_at: "2026-09-20T10:00:00Z",
      undone_at: null,
      undone_by: null,
      undo_until: "2026-09-20T10:30:00Z",
      can_undo: true,
    },
    ...overrides,
  };
}

beforeEach(() => {
  router.push.mockClear();
  window.sessionStorage.setItem(QUEUE_ORDER_KEY, JSON.stringify(["biz-1", "biz-2"]));
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("BusinessReview", () => {
  it("shows the conflict message when another reviewer decided first, and reloads", async () => {
    let loads = 0;
    routeFetch({
      "GET /review-queue/biz-1": () => {
        loads += 1;
        return { status: 200, body: reviewDetail() };
      },
      "GET /users": REPS,
      "POST /opportunities/opp-1/review": {
        status: 409,
        body: envelope(409, "stale_lock_version", "Another reviewer already decided this", { lock_version: 1 }),
      },
    });
    renderWithProviders(<BusinessReview businessId="biz-1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.submit(screen.getByRole("dialog"));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(CONFLICT_MESSAGE));
    expect(loads).toBe(2);
    expect(router.push).not.toHaveBeenCalled();
  });

  it("after a decision shows a toast with Undo, which calls the undo endpoint, and moves on", async () => {
    const { calls } = routeFetch({
      "GET /review-queue/biz-1": { status: 200, body: reviewDetail() },
      "GET /users": REPS,
      "POST /opportunities/opp-1/review": (init) => {
        const body = JSON.parse(String(init?.body));
        expect(body).toMatchObject({ decision: "approve", lock_version: 0, assigned_to: "user-sales_rep" });
        return { status: 200, body: decided() };
      },
      "POST /review-decisions/dec-1/undo": {
        status: 200,
        body: { undone: [], opportunities: [reviewOpportunity()], suppressions_lifted: 0 },
      },
    });
    renderWithProviders(<BusinessReview businessId="biz-1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByLabelText(/Assign to sales rep/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/Assign to sales rep/), { target: { value: "user-sales_rep" } });
    fireEvent.submit(screen.getByRole("dialog"));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("approved"));
    expect(router.push).toHaveBeenCalledWith("/review/biz-2");

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/review-decisions/dec-1/undo"))).toBe(true),
    );
  });

  it("stays on a business that still has open opportunities", async () => {
    routeFetch({
      "GET /review-queue/biz-1": {
        status: 200,
        body: reviewDetail({
          opportunities: [reviewOpportunity(), reviewOpportunity({ id: "opp-2", service: "seo_gbp", service_name: "SEO" })],
        }),
      },
      "GET /users": REPS,
      "POST /opportunities/opp-1/review": { status: 200, body: decided() },
    });
    renderWithProviders(<BusinessReview businessId="biz-1" />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(2));
    fireEvent.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    fireEvent.submit(screen.getByRole("dialog"));
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(router.push).not.toHaveBeenCalled();
  });

  it("summarises the open opportunities at the top with jump links to each card", async () => {
    routeFetch({
      "GET /review-queue/biz-1": {
        status: 200,
        body: reviewDetail({
          opportunities: [
            reviewOpportunity(),
            reviewOpportunity({ id: "opp-2", service: "seo_gbp", service_name: "SEO", score: 0.55 }),
            reviewOpportunity({ id: "opp-3", service: "booking_setup", review_status: "rejected" }),
          ],
        }),
      },
      "GET /users": REPS,
    });
    renderWithProviders(<BusinessReview businessId="biz-1" />);
    await waitFor(() => expect(screen.getByTestId("open-summary")).toBeTruthy());
    const summary = screen.getByTestId("open-summary");
    const links = Array.from(summary.querySelectorAll("a"));
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["#opportunity-opp-1", "#opportunity-opp-2"]);
    expect(links.map((a) => a.textContent)).toEqual(["Website redesignScore 72", "SEO / Google profileScore 55"]);
    expect(summary.textContent).not.toContain("Online booking");
    expect(document.getElementById("opportunity-opp-2")).not.toBeNull();
    // The business phone reads the way people dial it.
    expect(screen.getByText("(512) 555-0100")).toBeTruthy();
  });

  it("keyboard: a opens approve for the focused opportunity, but not while typing; j goes next", async () => {
    routeFetch({
      "GET /review-queue/biz-1": { status: 200, body: reviewDetail() },
      "GET /users": REPS,
    });
    renderWithProviders(<BusinessReview businessId="biz-1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy());

    fireEvent.keyDown(window, { key: "j" });
    expect(router.push).toHaveBeenCalledWith("/review/biz-2");

    fireEvent.keyDown(window, { key: "?" });
    expect(screen.getByRole("dialog").textContent).toContain("Keyboard shortcuts");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    fireEvent.keyDown(window, { key: "a" });
    expect(screen.getByRole("dialog").textContent).toContain("Approve");
    const note = screen.getByLabelText(/Note/);
    fireEvent.keyDown(note, { key: "r" });
    expect(screen.getByRole("dialog").textContent).not.toContain("Reject");
  });

  it("disables decisions and says so for a suppressed business", async () => {
    routeFetch({
      "GET /review-queue/biz-1": {
        status: 200,
        body: reviewDetail({
          suppressed: true,
          suppressions: [
            {
              id: "s1",
              business_id: "biz-1",
              business_name: "Barton Creek Plumbing",
              domain: "bartoncreekplumbing.invalid",
              phone_e164: "+15125550100",
              reason: "asked",
              source: "review",
              created_by: "u",
              created_at: "2026-09-20T10:00:00Z",
              lifted_at: null,
              lifted_by: null,
              active: true,
            },
          ],
          opportunities: [reviewOpportunity({ review_status: "do_not_contact" })],
        }),
      },
      "GET /users": REPS,
    });
    renderWithProviders(<BusinessReview businessId="biz-1" />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Do not contact"));
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByRole("button", { name: "Do not contact" })).toHaveProperty("disabled", true);
  });

  it("gives a read-only role no decision buttons at all", async () => {
    routeFetch({ "GET /review-queue/biz-1": { status: 200, body: reviewDetail() } });
    renderWithProviders(<BusinessReview businessId="biz-1" />, { user: me("crm_manager") });
    await waitFor(() => expect(screen.getAllByTestId("opportunity-card")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Do not contact" })).toBeNull();
  });
});
