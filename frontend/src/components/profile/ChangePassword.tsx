"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useToast } from "@/components/ui";
import { ApiError, changePassword } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { homeFor, ROLE_LABELS } from "@/lib/roles";

const MIN_PASSWORD_LENGTH = 12;

export function ChangePassword() {
  const { user, signedIn } = useAuth();
  const { show } = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const forced = params.get("forced") === "1" || Boolean(user?.must_change_password);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (next.length < MIN_PASSWORD_LENGTH) {
      setError(`The new password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (next !== again) {
      setError("The two new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      const me = await signedIn();
      setCurrent("");
      setNext("");
      setAgain("");
      setError(null);
      show({ tone: "success", message: "Password changed. Other sessions were signed out." });
      if (forced) router.replace(homeFor(me.role));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not change the password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-navy">Profile</h1>
        {user ? <p className="text-sm text-slate-600">{user.email} · {ROLE_LABELS[user.role]}</p> : null}
      </header>
      {forced ? (
        <p role="alert" data-testid="forced-notice" className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          Your password was set by an administrator. Choose your own before doing anything else.
        </p>
      ) : null}
      <form onSubmit={submit} className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4" aria-label="Change password">
        <label className="flex flex-col gap-1 text-sm">
          Current password
          <input className="field" type="password" required autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          New password
          <input className="field" type="password" required minLength={MIN_PASSWORD_LENGTH} autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          New password again
          <input className="field" type="password" required autoComplete="new-password" value={again} onChange={(event) => setAgain(event.target.value)} />
        </label>
        <p className="text-xs text-slate-600">At least {MIN_PASSWORD_LENGTH} characters, not your email address, not a common password.</p>
        {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-2 text-sm text-red-800">{error}</p> : null}
        <button type="submit" className="btn-primary self-start" disabled={busy}>Change password</button>
      </form>
    </div>
  );
}
