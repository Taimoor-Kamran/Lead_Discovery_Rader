import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { CrmDashboard, setSaveFile } from "./CrmDashboard";
import { me, renderWithProviders, routeFetch } from "@/test/utils";

const STATUS = {
  destination: "fake",
  demo: true,
  auto_sync: true,
  sync_delay_minutes: 30,
  health: {
    destination: "fake",
    ok: true,
    checks: [{ name: "store", ok: true, detail: "2 record(s) in crm_fake_records" }],
    message: "Destination: fake (demo). Records stay in this database.",
  },
  counts: { scheduled: 1, syncing: 0, synced: 2, held: 1, cancelled: 0, withdrawn: 0 },
};

function lead(overrides: Record<string, unknown> = {}) {
  return {
    id: "crm-1",
    business_id: "biz-1",
    business_name: "Barton Creek Plumbing",
    city: "Austin",
    state: "TX",
    destination: "fake",
    status: "held",
    external_id: null,
    external_url: null,
    due_at: null,
    attempts: 1,
    last_error: "CrmAuthError: Airtable rejected the token. Check AIRTABLE_TOKEN",
    last_synced_at: null,
    export_batch_id: null,
    services: ["Website redesign"],
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:00Z",
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("CRM dashboard", () => {
  it("shows the destination, health, counts, held leads with their error and Retry", async () => {
    const { calls } = routeFetch({
      "GET /crm/status": { status: 200, body: STATUS },
      "GET /crm/leads": (init, url) => {
        const status = new URL(url).searchParams.get("status");
        const items = status === "held" ? [lead()] : status === "scheduled" ? [lead({ id: "crm-2", status: "scheduled", due_at: "2026-09-20T12:30:00Z", business_name: "Oak Hill Plumbing", last_error: null })] : [];
        return { status: 200, body: { items, next_cursor: null } };
      },
      "POST /crm/leads/crm-1/retry": { status: 200, body: lead({ status: "synced" }) },
      "POST /crm/businesses/biz-1/sync-now": { status: 200, body: lead({ id: "crm-2", status: "synced" }) },
    });
    renderWithProviders(<CrmDashboard />, { user: me("crm_manager") });

    await waitFor(() => expect(screen.getByTestId("crm-destination")).toBeTruthy());
    expect(screen.getByTestId("crm-destination").textContent).toContain("Destination: fake (demo)");
    expect(screen.getByTestId("crm-health").textContent).toContain("Healthy");
    expect(screen.getByTestId("crm-counts").textContent).toContain("Held");
    expect(screen.getByTestId("crm-checks").textContent).toContain("crm_fake_records");

    const held = screen.getByTestId("crm-held");
    expect(held.textContent).toContain("Barton Creek Plumbing");
    expect(held.textContent).toContain("AIRTABLE_TOKEN");
    expect(screen.getByTestId("crm-scheduled").textContent).toContain("Oak Hill Plumbing");
    expect(screen.getByRole("button", { name: "Sync all due" })).toBeTruthy();
    // The fake destination has no file to export.
    expect(screen.queryByRole("button", { name: /Export CSV/ })).toBeNull();

    fireEvent.click(held.querySelector("button")!);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/crm/leads/crm-1/retry"))).toBe(true));
    await waitFor(() => expect(screen.getAllByRole("status")[0].textContent).toContain("In CRM"));

    fireEvent.click(screen.getByRole("button", { name: "Send now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/crm/businesses/biz-1/sync-now"))).toBe(true));
  });

  it("offers Export CSV for the csv destination and saves the file", async () => {
    const saved: string[] = [];
    setSaveFile((filename) => saved.push(filename));
    const spy = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/crm/export.csv")) {
        return new Response("﻿Radar Business ID\r\n", {
          status: 200,
          headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="radar-leads-20260920-1230.csv"' },
        });
      }
      if (url.includes("/crm/status")) {
        return new Response(JSON.stringify({ ...STATUS, destination: "csv", demo: false }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", spy);
    renderWithProviders(<CrmDashboard />, { user: me("admin") });

    const button = await screen.findByRole("button", { name: "Export CSV (new)" });
    fireEvent.click(button);
    await waitFor(() => expect(saved).toEqual(["radar-leads-20260920-1230.csv"]));
    expect(spy.mock.calls.some((call) => String(call[0]).includes("/crm/export.csv?scope=new"))).toBe(true);
    expect(screen.getByRole("button", { name: "Export CSV (all)" })).toBeTruthy();
  });

  it("is read-only for a tech admin", async () => {
    routeFetch({ "GET /crm/status": { status: 200, body: STATUS } });
    renderWithProviders(<CrmDashboard />, { user: me("tech_admin") });
    await waitFor(() => expect(screen.getByTestId("crm-destination")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Sync all due" })).toBeNull();
    expect(screen.queryByTestId("crm-held")).toBeNull();
    expect(screen.getByText(/Read-only for your role/)).toBeTruthy();
  });
});
