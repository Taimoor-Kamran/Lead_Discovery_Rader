import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { generateTemporaryPassword, Users } from "./Users";
import { me, renderWithProviders, routeFetch } from "@/test/utils";

const ADMIN = me("admin", "ops@agency.test", { id: "user-admin" });

function user(overrides: Record<string, unknown> = {}) {
  return {
    id: "u-1",
    email: "reviewer@example.com",
    role: "reviewer",
    is_active: true,
    must_change_password: false,
    locked_until: null,
    locked: false,
    rate_limited_until: null,
    rate_limited: false,
    last_login_at: "2026-09-20T09:00:00Z",
    created_at: "2026-09-20T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("Users page", () => {
  it("lists users with lock and password state, and creates one who must change their password", async () => {
    const { calls } = routeFetch({
      "GET /users": {
        status: 200,
        body: {
          items: [
            user(),
            user({ id: "u-2", email: "rep1@example.com", role: "sales_rep", locked: true, locked_until: "2026-09-20T10:00:00Z", must_change_password: true, last_login_at: null }),
          ],
          next_cursor: null,
        },
      },
      "POST /users": (init) => ({ status: 201, body: user({ id: "u-3", email: JSON.parse(String(init?.body)).email, must_change_password: true }) }),
    });
    renderWithProviders(<Users />, { user: ADMIN });

    await waitFor(() => expect(screen.getAllByTestId("user-row")).toHaveLength(2));
    const locked = screen.getAllByTestId("user-row")[1];
    expect(locked.textContent).toContain("locked until");
    expect(locked.textContent).toContain("must change");
    expect(locked.textContent).toContain("never");
    expect(screen.queryByTestId("placeholder-warning")).toBeNull();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@agency.test" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "sales_rep" } });
    fireEvent.change(screen.getByLabelText("Temporary password"), { target: { value: "temporary-pass-9f3k2" } });
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() => expect(screen.getByTestId("created-once")).toBeTruthy());
    const created = calls.find((call) => call.method === "POST" && call.url.endsWith("/users"));
    expect(JSON.parse(String(created?.init?.body))).toEqual({
      email: "new@agency.test",
      role: "sales_rep",
      password: "temporary-pass-9f3k2",
      is_active: true,
      must_change_password: true,
    });
    expect(screen.getByTestId("created-once").textContent).toContain("temporary-pass-9f3k2");
  });

  it("warns about the placeholder admin and deactivates it in one click", async () => {
    const { calls } = routeFetch({
      "GET /users": { status: 200, body: { items: [user({ id: "u-9", email: "admin@example.com", role: "admin" })], next_cursor: null } },
      "PATCH /users/u-9": { status: 200, body: user({ id: "u-9", email: "admin@example.com", role: "admin", is_active: false }) },
    });
    renderWithProviders(<Users />, { user: ADMIN });

    await waitFor(() => expect(screen.getByTestId("placeholder-warning")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Deactivate admin@example.com" }));

    await waitFor(() => expect(calls.some((call) => call.method === "PATCH" && call.url.endsWith("/users/u-9"))).toBe(true));
    const patch = calls.find((call) => call.method === "PATCH");
    expect(JSON.parse(String(patch?.init?.body))).toEqual({ is_active: false });
  });

  it("unlocks, resets a password and changes a role from the row", async () => {
    const { calls } = routeFetch({
      "GET /users": { status: 200, body: { items: [user({ locked: true, locked_until: "2026-09-20T10:00:00Z" })], next_cursor: null } },
      "POST /users/u-1/unlock": { status: 200, body: user() },
      "POST /users/u-1/reset-password": { status: 200, body: user({ must_change_password: true }) },
      "PATCH /users/u-1": { status: 200, body: user({ role: "crm_manager" }) },
    });
    renderWithProviders(<Users />, { user: ADMIN });

    await waitFor(() => expect(screen.getByRole("button", { name: "Unlock" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Unlock" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/users/u-1/unlock"))).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Reset password" }));
    const dialog = screen.getByRole("dialog", { name: /Reset password for reviewer@example.com/ });
    fireEvent.change(dialog.querySelector("input")!, { target: { value: "another-temp-pass-77" } });
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));
    await waitFor(() => expect(screen.getByTestId("reset-once").textContent).toContain("another-temp-pass-77"));
    const reset = calls.find((call) => call.url.endsWith("/users/u-1/reset-password"));
    expect(JSON.parse(String(reset?.init?.body))).toEqual({ password: "another-temp-pass-77" });

    fireEvent.change(screen.getByLabelText("Role for reviewer@example.com"), { target: { value: "crm_manager" } });
    await waitFor(() => expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(1));
    expect(JSON.parse(String(calls.find((call) => call.method === "PATCH")?.init?.body))).toEqual({ role: "crm_manager" });
  });

  it("shows a temporarily blocked (rate-limited) user and unlock clears it, even without a lock", async () => {
    const until = new Date();
    until.setMinutes(until.getMinutes() + 14);
    const expected = until.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    let unlocked = false;
    const { calls } = routeFetch({
      "GET /users": () => ({
        status: 200,
        body: {
          items: [
            unlocked
              ? user({ id: "u-7", email: "rep2@example.com" })
              : user({ id: "u-7", email: "rep2@example.com", rate_limited: true, rate_limited_until: until.toISOString() }),
            user({ id: "u-8", email: "both@example.com", locked: true, locked_until: "2026-09-20T10:00:00Z", rate_limited: true, rate_limited_until: until.toISOString() }),
          ],
          next_cursor: null,
        },
      }),
      "POST /users/u-7/unlock": () => {
        unlocked = true;
        return { status: 200, body: user({ id: "u-7", email: "rep2@example.com" }) };
      },
    });
    renderWithProviders(<Users />, { user: ADMIN });

    await waitFor(() => expect(screen.getAllByTestId("user-row")).toHaveLength(2));
    const [blocked, both] = screen.getAllByTestId("user-row");
    expect(blocked.textContent).toContain(`Temporarily blocked (until ${expected})`);
    expect(blocked.textContent).not.toContain("locked until");
    // Both states show side by side when an account is locked and its address is blocked.
    expect(both.textContent).toContain("locked until");
    expect(both.textContent).toContain("Temporarily blocked (until");
    expect(screen.getAllByRole("button", { name: "Unlock" })).toHaveLength(2);

    fireEvent.click(screen.getAllByRole("button", { name: "Unlock" })[0]);
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/users/u-7/unlock"))).toBe(true));
    await waitFor(() => expect(screen.getAllByTestId("user-row")[0].textContent).not.toContain("Temporarily blocked"));
    expect(screen.getAllByTestId("user-row")[0].querySelector('[data-testid="lock-state"]')?.textContent).toBe("—");
    expect(screen.getAllByRole("button", { name: "Unlock" })).toHaveLength(1);
  });

  it("generates a temporary password that meets the length rule", () => {
    const password = generateTemporaryPassword();
    expect(password.length).toBeGreaterThanOrEqual(12);
    expect(password).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(generateTemporaryPassword()).not.toBe(password);
  });
});
