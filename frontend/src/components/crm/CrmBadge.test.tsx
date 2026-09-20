import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { CrmBadge } from "./CrmBadge";

const BASE = {
  id: "crm-1",
  external_url: null,
  last_synced_at: null,
  due_at: null,
  last_error: null,
};

describe("CrmBadge", () => {
  it("says when a scheduled lead will go", () => {
    render(<CrmBadge crm={{ ...BASE, status: "scheduled", due_at: "2026-09-20T12:30:00Z" }} />);
    const badge = screen.getByTestId("crm-badge");
    expect(badge.getAttribute("data-status")).toBe("scheduled");
    expect(badge.textContent).toContain("Scheduled");
    expect(badge.textContent).toContain("2026");
  });

  it("links a synced lead to its CRM record only for an http(s) URL", () => {
    render(
      <CrmBadge
        crm={{ ...BASE, status: "synced", last_synced_at: "2026-09-20T12:31:00Z", external_url: "https://airtable.com/app1/tbl1/rec1" }}
      />,
    );
    expect(screen.getByTestId("crm-badge").textContent).toContain("In CRM ✓");
    expect(screen.getByRole("link", { name: "Open record" }).getAttribute("href")).toBe("https://airtable.com/app1/tbl1/rec1");
  });

  it("never turns a javascript: record URL into a link", () => {
    render(<CrmBadge crm={{ ...BASE, status: "synced", external_url: "javascript:alert(1)" }} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows Held with the error in the tooltip and a Retry button only for managers", () => {
    const onRetry = vi.fn();
    const { unmount } = render(
      <CrmBadge crm={{ ...BASE, status: "held", last_error: "CrmAuthError: bad token" }} canRetry onRetry={onRetry} />,
    );
    expect(screen.getByTestId("crm-badge").textContent).toContain("Held ⚠");
    expect(screen.getByTitle("CrmAuthError: bad token")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledWith("crm-1");
    unmount();
    render(<CrmBadge crm={{ ...BASE, status: "held", last_error: "x" }} canRetry={false} onRetry={onRetry} />);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("says when nothing was scheduled", () => {
    render(<CrmBadge crm={null} />);
    expect(screen.getByTestId("crm-badge").textContent).toBe("Not scheduled");
  });
});
