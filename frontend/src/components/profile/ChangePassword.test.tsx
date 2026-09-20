import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { ChangePassword } from "./ChangePassword";
import { RequireRole } from "@/components/RequireRole";
import { envelope, me, renderWithProviders, routeFetch, router, setPathname, setSearchParams } from "@/test/utils";

beforeEach(() => {
  router.replace.mockClear();
  setSearchParams("");
  setPathname("/profile");
});
afterEach(() => vi.unstubAllGlobals());

describe("Change password", () => {
  it("changes the password and, when forced, sends the user to their home page", async () => {
    setSearchParams("forced=1");
    const { calls } = routeFetch({
      "POST /auth/change-password": { status: 200, body: { access_token: "new-token", token_type: "bearer", expires_at: "2026-09-20T11:00:00Z" } },
      "GET /auth/me": { status: 200, body: me("reviewer", "newbie@example.com", { must_change_password: false }) },
    });
    renderWithProviders(<ChangePassword />, { user: me("reviewer", "newbie@example.com", { must_change_password: true }) });

    expect(screen.getByTestId("forced-notice")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "temporary-pass-9f3k2" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a-brand-new-passphrase-42" } });
    fireEvent.change(screen.getByLabelText("New password again"), { target: { value: "a-brand-new-passphrase-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/review"));
    const change = calls.find((call) => call.url.endsWith("/auth/change-password"));
    expect(JSON.parse(String(change?.init?.body))).toEqual({ current_password: "temporary-pass-9f3k2", new_password: "a-brand-new-passphrase-42" });
  });

  it("checks the two new passwords match and shows the API's rule", async () => {
    routeFetch({
      "POST /auth/change-password": { status: 422, body: envelope(422, "weak_password", "That password cannot be used: it is one of the most common passwords and is not allowed") },
    });
    renderWithProviders(<ChangePassword />, { user: me("sales_rep") });
    expect(screen.queryByTestId("forced-notice")).toBeNull();

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "correct-horse-battery-staple" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "password1234" } });
    fireEvent.change(screen.getByLabelText("New password again"), { target: { value: "password12345" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(screen.getByRole("alert").textContent).toContain("do not match");

    fireEvent.change(screen.getByLabelText("New password again"), { target: { value: "password1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("most common passwords"));
  });
});

describe("Forced password change", () => {
  it("sends a user who must change their password to /profile from any other page", () => {
    setPathname("/review");
    renderWithProviders(
      <RequireRole roles={["reviewer"]}>
        <p>secret</p>
      </RequireRole>,
      { user: me("reviewer", "newbie@example.com", { must_change_password: true }) },
    );
    expect(screen.queryByText("secret")).toBeNull();
    expect(screen.getByTestId("forced-change")).toBeTruthy();
    expect(router.replace).toHaveBeenCalledWith("/profile?forced=1");
  });

  it("lets them reach /profile", () => {
    setPathname("/profile");
    renderWithProviders(
      <RequireRole roles={["reviewer"]}>
        <p>profile</p>
      </RequireRole>,
      { user: me("reviewer", "newbie@example.com", { must_change_password: true }) },
    );
    expect(screen.getByText("profile")).toBeTruthy();
    expect(router.replace).not.toHaveBeenCalled();
  });
});
