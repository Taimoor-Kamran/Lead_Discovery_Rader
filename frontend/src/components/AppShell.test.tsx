import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { AppShell } from "./AppShell";
import { me, renderWithProviders } from "@/test/utils";

function navLabels() {
  return screen
    .getAllByRole("link")
    .filter((link) => link.closest("nav"))
    .map((link) => link.textContent);
}

describe("AppShell navigation by role", () => {
  it("shows a sales rep only their leads", () => {
    renderWithProviders(<AppShell>x</AppShell>, { user: me("sales_rep") });
    expect(navLabels()).toEqual(["My leads", "Searches"]);
    expect(screen.getByTestId("whoami").textContent).toContain("Sales rep");
  });

  it("shows a reviewer the queue, duplicates and leads but not suppressions", () => {
    renderWithProviders(<AppShell>x</AppShell>, { user: me("reviewer") });
    expect(navLabels()).toEqual(["Review queue", "Duplicates", "My leads"]);
  });

  it("shows an admin everything", () => {
    renderWithProviders(<AppShell>x</AppShell>, { user: me("admin") });
    expect(navLabels()).toEqual(["Review queue", "Duplicates", "My leads", "Searches", "CRM", "Suppressions", "Users", "Health"]);
  });

  it("shows a crm manager the queue (read-only), leads and the CRM; a tech admin the queue and the CRM", () => {
    const { unmount } = renderWithProviders(<AppShell>x</AppShell>, { user: me("crm_manager") });
    expect(navLabels()).toEqual(["Review queue", "My leads", "CRM"]);
    unmount();
    renderWithProviders(<AppShell>x</AppShell>, { user: me("tech_admin") });
    expect(navLabels()).toEqual(["Review queue", "Searches", "CRM", "Health"]);
  });
});
