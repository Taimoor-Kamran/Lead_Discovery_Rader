import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { SignInPanel } from "./SignInPanel";
import { clearAccessToken, getAccessToken } from "@/lib/session";
import { envelope, me, renderWithProviders, routeFetch, router } from "@/test/utils";

const HEALTH = { status: 200, body: { status: "ok", db: true, redis: true } };

beforeEach(() => {
  clearAccessToken();
  router.replace.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

async function signIn(password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "rep@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("SignInPanel", () => {
  it("shows API health on load", async () => {
    routeFetch({ "GET /health": HEALTH });
    renderWithProviders(<SignInPanel />, { user: null, status: "anonymous" });
    await waitFor(() => expect(screen.getByTestId("health").textContent).toContain("status ok"));
  });

  it("signs in, holds the token in memory only and goes to the role's home", async () => {
    routeFetch({
      "GET /health": HEALTH,
      "POST /auth/login": { status: 200, body: { access_token: "token-abc" } },
      "GET /auth/me": { status: 200, body: me("sales_rep", "rep@example.com") },
    });
    renderWithProviders(<SignInPanel />, { user: null, status: "anonymous" });
    await signIn("a-good-password");

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/leads"));
    expect(getAccessToken()).toBe("token-abc");
    expect(window.localStorage.length).toBe(0);
  });

  it("shows the API error message when the credentials are wrong", async () => {
    routeFetch({
      "GET /health": HEALTH,
      "POST /auth/login": {
        status: 401,
        body: envelope(401, "invalid_credentials", "Email or password is incorrect"),
      },
    });
    renderWithProviders(<SignInPanel />, { user: null, status: "anonymous" });
    await signIn("wrong");

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe("Email or password is incorrect"),
    );
    expect(getAccessToken()).toBeNull();
    expect(router.replace).not.toHaveBeenCalled();
  });
});
