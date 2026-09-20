"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, getHealth, login, type Health } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { homeFor } from "@/lib/roles";
import { clearAccessToken } from "@/lib/session";

export function SignInPanel() {
  const router = useRouter();
  const { status, user, signedIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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

  // Already signed in (a reload restored the session): go straight to the role's home.
  useEffect(() => {
    if (status === "authenticated" && user) router.replace(homeFor(user.role));
  }, [status, user, router]);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      const me = await signedIn();
      setPassword("");
      router.replace(homeFor(me.role));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not reach the API");
      clearAccessToken();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-6">
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
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="field"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            name="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="field"
          />
        </label>
        <button type="submit" disabled={busy} className="btn-primary justify-center">
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {error ? (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        ) : null}
      </form>

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
