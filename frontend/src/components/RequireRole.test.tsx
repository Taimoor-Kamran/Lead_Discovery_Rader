import { beforeEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { RequireRole } from "./RequireRole";
import { me, renderWithProviders, router } from "@/test/utils";

beforeEach(() => router.replace.mockClear());

describe("RequireRole", () => {
  it("renders the page for an allowed role", () => {
    renderWithProviders(
      <RequireRole roles={["reviewer"]}>
        <p>secret</p>
      </RequireRole>,
      { user: me("reviewer") },
    );
    expect(screen.getByText("secret")).toBeTruthy();
  });

  it("refuses a wrong role without relying on the API", () => {
    renderWithProviders(
      <RequireRole roles={["admin", "reviewer"]}>
        <p>secret</p>
      </RequireRole>,
      { user: me("sales_rep") },
    );
    expect(screen.queryByText("secret")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Not available for your role");
  });

  it("sends an anonymous visitor to the login page", () => {
    renderWithProviders(
      <RequireRole roles={["reviewer"]}>
        <p>secret</p>
      </RequireRole>,
      { user: null, status: "anonymous" },
    );
    expect(screen.queryByText("secret")).toBeNull();
    expect(router.replace).toHaveBeenCalledWith("/login");
  });
});
