"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, getHealth, getMe, login, logout, type Health, type Me } from "@/lib/api";
import { clearAccessToken, getAccessToken, setAccessToken } from "@/lib/session";

export function SignInPanel() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
  }, [refreshHealth]);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { access_token: token } = await login(email, password);
      setAccessToken(token);
      setMe(await getMe(token));
      setPassword("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not reach the API");
      clearAccessToken();
      setMe(null);
    } finally {
      setBusy(false);
    }
  }

  async function onSignOut() {
    try {
      await logout();
    } finally {
      clearAccessToken();
      setMe(null);
    }
  }

  return (
    <section className="flex flex-col gap-6">
      {me ? (
        <div className="rounded-lg border border-slate-200 bg-white p-4" data-testid="me">
          <h2 className="font-medium">Signed in</h2>
          <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-1 text-sm">
            <dt className="text-slate-500">Email</dt>
            <dd>{me.email}</dd>
            <dt className="text-slate-500">Role</dt>
            <dd>{me.role}</dd>
            <dt className="text-slate-500">Token</dt>
            <dd>{getAccessToken() ? "held in memory" : "none"}</dd>
          </dl>
          <button
            type="button"
            onClick={onSignOut}
            className="mt-4 rounded border border-slate-300 px-3 py-1.5 text-sm"
          >
            Sign out
          </button>
        </div>
      ) : (
        <form
          onSubmit={onSubmit}
          className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4"
        >
          <label className="flex flex-col gap-1 text-sm">
            Email
            <input
              type="email"
              name="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="rounded border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Password
            <input
              type="password"
              name="password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded border border-slate-300 px-2 py-1.5"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
          {error ? (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          ) : null}
        </form>
      )}

      <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm" data-testid="health">
        <h2 className="font-medium">API health</h2>
        {health ? (
          <p className="mt-1 text-slate-600">
            status {health.status} · db {health.db ? "ok" : "down"} · redis{" "}
            {health.redis ? "ok" : "down"}
          </p>
        ) : (
          <p className="mt-1 text-slate-600">unreachable</p>
        )}
      </div>
    </section>
  );
}
