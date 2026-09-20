"use client";

import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/Toast";
import {
  ApiError,
  createUser,
  getUsers,
  resetUserPassword,
  unlockUser,
  updateUser,
  type Role,
  type UserRead,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime, formatTime } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/roles";

const ROLES: Role[] = ["admin", "reviewer", "sales_rep", "crm_manager", "tech_admin"];
const PLACEHOLDER_ADMIN = "admin@example.com";
const MIN_PASSWORD_LENGTH = 12;

/** A temporary password: 18 random bytes, URL-safe. Shown once; the user replaces it. */
export function generateTemporaryPassword(): string {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function describe(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) {
    const rules = caught.details.rules;
    if (Array.isArray(rules) && rules.length) return `${caught.message}`;
    return caught.message;
  }
  return fallback;
}

export function Users() {
  const { show } = useToast();
  const { user: viewer } = useAuth();
  const [items, setItems] = useState<UserRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("reviewer");
  const [password, setPassword] = useState("");
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);
  const [resetting, setResetting] = useState<UserRead | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [reset, setReset] = useState<{ email: string; password: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await getUsers()).items);
      setError(null);
    } catch (caught) {
      setError(describe(caught, "Could not load users"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      show({ tone: "success", message: label });
      await load();
    } catch (caught) {
      show({ tone: "error", message: describe(caught, `Could not ${label.toLowerCase()}`) });
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`The temporary password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    setBusy(true);
    try {
      const made = await createUser({ email: email.trim(), role, password, is_active: true, must_change_password: true });
      setCreated({ email: made.email, password });
      setEmail("");
      setPassword("");
      setError(null);
      show({ tone: "success", message: `Created ${made.email}. They must change the password on first sign-in.` });
      await load();
    } catch (caught) {
      setError(describe(caught, "Could not create the user"));
    } finally {
      setBusy(false);
    }
  }

  async function submitReset(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!resetting) return;
    if (resetPassword.length < MIN_PASSWORD_LENGTH) {
      show({ tone: "error", message: `A password needs at least ${MIN_PASSWORD_LENGTH} characters.` });
      return;
    }
    const target = resetting;
    setBusy(true);
    try {
      await resetUserPassword(target.id, resetPassword);
      setReset({ email: target.email, password: resetPassword });
      setResetting(null);
      setResetPassword("");
      show({ tone: "success", message: `Temporary password set for ${target.email}.` });
      await load();
    } catch (caught) {
      show({ tone: "error", message: describe(caught, "Could not reset the password") });
    } finally {
      setBusy(false);
    }
  }

  const placeholder = items.find((item) => item.email.toLowerCase() === PLACEHOLDER_ADMIN && item.is_active);

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-navy">Users</h1>
        <p className="text-sm text-slate-600">
          Create accounts with a temporary password (they must change it on first sign-in),
          change roles, deactivate, unlock and reset. Every action is audited. <em>Unlock</em> lifts
          both the 15-minute account lock and a temporary block after too many failed attempts.
        </p>
      </header>

      {placeholder ? (
        <div role="alert" data-testid="placeholder-warning" className="flex flex-wrap items-center gap-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <span>
            The placeholder administrator <strong>{placeholder.email}</strong> is still active. Create a real admin,
            sign in as them, then deactivate this one.
          </span>
          <button
            type="button"
            className="btn-danger !py-0.5"
            disabled={busy || placeholder.id === viewer?.id}
            title={placeholder.id === viewer?.id ? "You are signed in as this user" : undefined}
            onClick={() => run("Placeholder admin deactivated.", () => updateUser(placeholder.id, { is_active: false }))}
          >
            Deactivate {placeholder.email}
          </button>
        </div>
      ) : null}

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-3" aria-label="Create user">
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Email
          <input className="field w-64" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Role
          <select className="field" value={role} onChange={(event) => setRole(event.target.value as Role)}>
            {ROLES.map((option) => (
              <option key={option} value={option}>{ROLE_LABELS[option]}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Temporary password
          <input className="field w-72 font-mono" type="text" required minLength={MIN_PASSWORD_LENGTH} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="off" />
        </label>
        <button type="button" className="btn-secondary" onClick={() => setPassword(generateTemporaryPassword())}>Generate</button>
        <button type="submit" className="btn-primary" disabled={busy}>Create user</button>
      </form>
      {created ? (
        <p data-testid="created-once" className="rounded border border-teal-300 bg-teal-50 p-3 text-sm">
          Give <strong>{created.email}</strong> this temporary password once, in person or over a channel you trust:{" "}
          <code className="font-mono">{created.password}</code>. It is not stored anywhere readable and cannot be shown again.
        </p>
      ) : null}
      {reset ? (
        <p data-testid="reset-once" className="rounded border border-teal-300 bg-teal-50 p-3 text-sm">
          New temporary password for <strong>{reset.email}</strong>: <code className="font-mono">{reset.password}</code>. Shown once.
        </p>
      ) : null}
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}

      {resetting ? (
        <form onSubmit={submitReset} role="dialog" aria-label={`Reset password for ${resetting.email}`} className="flex flex-wrap items-end gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3">
          <label className="flex flex-col gap-1 text-xs text-slate-700">
            New temporary password for {resetting.email}
            <input className="field w-72 font-mono" type="text" required minLength={MIN_PASSWORD_LENGTH} value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} autoComplete="off" />
          </label>
          <button type="button" className="btn-secondary" onClick={() => setResetPassword(generateTemporaryPassword())}>Generate</button>
          <button type="submit" className="btn-primary" disabled={busy}>Set password</button>
          <button type="button" className="btn-secondary" onClick={() => setResetting(null)}>Cancel</button>
        </form>
      ) : null}

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th scope="col" className="px-3 py-2">Email</th>
              <th scope="col" className="px-3 py-2">Role</th>
              <th scope="col" className="px-3 py-2">Active</th>
              <th scope="col" className="px-3 py-2">Last sign-in</th>
              <th scope="col" className="px-3 py-2">Locked / blocked</th>
              <th scope="col" className="px-3 py-2">Password</th>
              <th scope="col" className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((item) => {
              const self = item.id === viewer?.id;
              return (
                <tr key={item.id} data-testid="user-row">
                  <td className="px-3 py-2">{item.email}{self ? <span className="ml-1 text-xs text-slate-500">(you)</span> : null}</td>
                  <td className="px-3 py-2">
                    <select
                      className="field !py-0.5"
                      aria-label={`Role for ${item.email}`}
                      value={item.role}
                      disabled={busy || self}
                      onChange={(event) => run(`Role changed for ${item.email}.`, () => updateUser(item.id, { role: event.target.value as Role }))}
                    >
                      {ROLES.map((option) => (
                        <option key={option} value={option}>{ROLE_LABELS[option]}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2">{item.is_active ? "active" : "deactivated"}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">{item.last_login_at ? formatDateTime(item.last_login_at) : "never"}</td>
                  <td className="px-3 py-2" data-testid="lock-state">
                    {item.locked || item.rate_limited ? (
                      <div className="flex flex-wrap gap-1">
                        {item.locked ? (
                          <span className="chip border-red-300 bg-red-50 text-red-800">locked until {formatDateTime(item.locked_until)}</span>
                        ) : null}
                        {item.rate_limited ? (
                          <span
                            className="chip border-amber-300 bg-amber-50 text-amber-900"
                            title="Too many failed sign-ins from one address in 15 minutes. Unlock clears it."
                          >
                            Temporarily blocked (until {formatTime(item.rate_limited_until)})
                          </span>
                        ) : null}
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">{item.must_change_password ? "must change" : "set"}</td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex flex-wrap justify-end gap-1">
                      {item.locked || item.rate_limited ? (
                        <button
                          type="button"
                          className="btn-secondary !py-0.5"
                          disabled={busy}
                          title="Clears the account lock and every failed-attempt counter for this email"
                          onClick={() => run(`${item.email} unlocked.`, () => unlockUser(item.id))}
                        >
                          Unlock
                        </button>
                      ) : null}
                      <button type="button" className="btn-secondary !py-0.5" disabled={busy} onClick={() => { setResetting(item); setResetPassword(""); }}>Reset password</button>
                      {item.is_active ? (
                        <button type="button" className="btn-danger !py-0.5" disabled={busy || self} onClick={() => run(`${item.email} deactivated.`, () => updateUser(item.id, { is_active: false }))}>Deactivate</button>
                      ) : (
                        <button type="button" className="btn-secondary !py-0.5" disabled={busy} onClick={() => run(`${item.email} reactivated.`, () => updateUser(item.id, { is_active: true }))}>Reactivate</button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!items.length ? <p className="p-8 text-center text-sm text-slate-600">No users.</p> : null}
      </div>
    </div>
  );
}
