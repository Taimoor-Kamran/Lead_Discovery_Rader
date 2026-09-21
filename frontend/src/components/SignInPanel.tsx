"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, Input } from "@/components/ui";
import { ApiError, getHealth, login, type Health } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { apiUnreachable } from "@/lib/errors";
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
      setError(caught instanceof ApiError ? caught.message : apiUnreachable());
      clearAccessToken();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Input
            label="Email"
            type="email"
            name="email"
            required
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <Input
            label="Password"
            type="password"
            name="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <Button type="submit" variant="primary" loading={busy} loadingLabel="Signing in…">
            Sign in
          </Button>
          {error ? (
            <p role="alert" className="text-base text-risk">
              {error}
            </p>
          ) : null}
        </form>
      </Card>

      <Card data-testid="health">
        <h2 className="text-md font-semibold text-ink">API health</h2>
        {health ? (
          <dl className="mt-2 grid grid-cols-[7rem_1fr] gap-y-1 text-base">
            <dt className="text-ink-soft">Status</dt>
            <dd>
              <Badge tone={health.status === "ok" ? "ok" : "risk"}>{health.status}</Badge>
            </dd>
            <dt className="text-ink-soft">Database</dt>
            <dd>
              <Badge tone={health.db ? "ok" : "risk"}>{health.db ? "ok" : "down"}</Badge>
            </dd>
            <dt className="text-ink-soft">Redis</dt>
            <dd>
              <Badge tone={health.redis ? "ok" : "risk"}>{health.redis ? "ok" : "down"}</Badge>
            </dd>
          </dl>
        ) : (
          <p className="mt-2 max-w-measure text-base text-ink-soft">{apiUnreachable()}</p>
        )}
      </Card>
    </div>
  );
}
