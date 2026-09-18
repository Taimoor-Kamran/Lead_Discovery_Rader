import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SignInPanel } from "./SignInPanel";
import { getAccessToken } from "@/lib/session";

type Route = { status: number; body: unknown };

function routeFetch(routes: Record<string, Route>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const match = Object.keys(routes).find((path) => url.endsWith(path));
      const route = match ? routes[match] : { status: 404, body: {} };
      return new Response(JSON.stringify(route.body), {
        status: route.status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

const HEALTH: Route = { status: 200, body: { status: "ok", db: true, redis: true } };

async function signIn(password: string) {
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "rep@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("SignInPanel", () => {
  it("shows API health on load", async () => {
    routeFetch({ "/health": HEALTH });
    render(<SignInPanel />);
    await waitFor(() =>
      expect(screen.getByTestId("health").textContent).toContain("status ok"),
    );
  });

  it("signs in, holds the token in memory and shows /auth/me", async () => {
    routeFetch({
      "/health": HEALTH,
      "/auth/login": { status: 200, body: { access_token: "token-abc" } },
      "/auth/me": {
        status: 200,
        body: { id: "1", email: "rep@example.com", role: "sales_rep", is_active: true },
      },
    });
    render(<SignInPanel />);
    await signIn("a-good-password");

    await waitFor(() => expect(screen.getByTestId("me")).toBeTruthy());
    expect(screen.getByTestId("me").textContent).toContain("rep@example.com");
    expect(screen.getByTestId("me").textContent).toContain("sales_rep");
    expect(getAccessToken()).toBe("token-abc");
    expect(window.localStorage.length).toBe(0);
  });

  it("shows the API error message when the credentials are wrong", async () => {
    routeFetch({
      "/health": HEALTH,
      "/auth/login": {
        status: 401,
        body: {
          error: {
            code: "invalid_credentials",
            message: "Email or password is incorrect",
            request_id: "req-1",
            details: {},
          },
        },
      },
    });
    render(<SignInPanel />);
    await signIn("wrong");

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe("Email or password is incorrect"),
    );
    expect(getAccessToken()).toBeNull();
  });
});
