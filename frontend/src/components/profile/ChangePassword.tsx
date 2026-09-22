"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button, Card, Input, PageHeader, useToast } from "@/components/ui";
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
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4">
      <PageHeader title="Profile" description={user ? `${user.email} — ${ROLE_LABELS[user.role]}` : undefined} />
      {forced ? (
        <p
          role="alert"
          data-testid="forced-notice"
          className="rounded-lg border border-warn bg-warn-tint p-3 text-base text-ink"
        >
          Your password was set by an administrator. Choose your own before doing anything else.
        </p>
      ) : null}
      <Card as="form" onSubmit={submit} aria-label="Change password" className="flex flex-col gap-4">
        <Input
          label="Current password"
          type="password"
          required
          autoComplete="current-password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
        />
        <Input
          label="New password"
          type="password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          autoComplete="new-password"
          hint={`At least ${MIN_PASSWORD_LENGTH} characters, not your email address, not a common password.`}
          value={next}
          onChange={(event) => setNext(event.target.value)}
        />
        <Input
          label="New password again"
          type="password"
          required
          autoComplete="new-password"
          value={again}
          onChange={(event) => setAgain(event.target.value)}
        />
        {error ? (
          <p role="alert" className="rounded border border-risk bg-risk-tint p-2 text-base text-ink">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="primary" loading={busy} className="self-start">
          Change password
        </Button>
      </Card>
    </div>
  );
}
