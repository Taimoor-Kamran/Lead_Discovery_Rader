import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { AlertBanner } from "./AlertBanner";
import { me, renderWithProviders, routeFetch } from "@/test/utils";

const ALERTS = [
  { id: "al-1", rule: "stale_job", severity: "warning", message: "A discovery run was stuck", details: {}, first_seen_at: "2026-09-20T09:00:00Z", last_seen_at: "2026-09-20T09:00:00Z", acknowledged_at: null, acknowledged_by: null, cleared_at: null, active: true, acknowledged: false },
  { id: "al-2", rule: "queue_length", severity: "critical", message: "The job queue holds 600 jobs", details: {}, first_seen_at: "2026-09-20T09:00:00Z", last_seen_at: "2026-09-20T09:00:00Z", acknowledged_at: null, acknowledged_by: null, cleared_at: null, active: true, acknowledged: false },
];

afterEach(() => vi.unstubAllGlobals());

describe("Alert banner", () => {
  it("shows open alerts to an admin and acknowledges the first", async () => {
    const { calls } = routeFetch({
      "GET /admin/alerts": () => ({ status: 200, body: calls.some((c) => c.method === "POST") ? [ALERTS[1]] : ALERTS }),
      "POST /admin/alerts/al-1/acknowledge": { status: 200, body: { ...ALERTS[0], acknowledged: true } },
    });
    renderWithProviders(<AlertBanner />, { user: me("admin") });

    await waitFor(() => expect(screen.getByTestId("alert-banner")).toBeTruthy());
    expect(screen.getByTestId("alert-banner").textContent).toContain("2 open alerts");
    expect(screen.getByTestId("alert-banner").textContent).toContain("(+1 more)");

    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(screen.getByTestId("alert-banner").textContent).toContain("1 open alert:"));
  });

  it("is not shown to a reviewer and never asks for alerts", async () => {
    const { calls } = routeFetch({ "GET /admin/alerts": { status: 200, body: ALERTS } });
    renderWithProviders(<AlertBanner />, { user: me("reviewer") });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByTestId("alert-banner")).toBeNull();
    expect(calls).toHaveLength(0);
  });
});
