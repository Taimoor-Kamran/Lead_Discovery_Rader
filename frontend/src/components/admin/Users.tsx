"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  Input,
  PageHeader,
  Select,
  SkeletonTableRows,
  Table,
  TableWrap,
  TBody,
  Td,
  Th,
  THead,
  Tr,
  useToast,
} from "@/components/ui";
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
import { loadFailed } from "@/lib/errors";
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
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await getUsers()).items);
      setError(null);
    } catch (caught) {
      setError(describe(caught, loadFailed("the users")));
    } finally {
      setLoading(false);
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
      <PageHeader
        title="Users"
        description="Create accounts with a temporary password (they must change it on first sign-in), change roles, deactivate, unlock and reset. Every action is audited. Unlock lifts both the 15-minute account lock and a temporary block after too many failed attempts."
      />

      {placeholder ? (
        <div
          role="alert"
          data-testid="placeholder-warning"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-warn bg-warn-tint p-3 text-base text-ink"
        >
          <span className="flex-1">
            The placeholder administrator <strong>{placeholder.email}</strong> is still active. Create a
            real admin, sign in as them, then deactivate this one.
          </span>
          <Button
            variant="danger"
            size="sm"
            disabled={busy || placeholder.id === viewer?.id}
            title={placeholder.id === viewer?.id ? "You are signed in as this user" : undefined}
            onClick={() => run("Placeholder admin deactivated.", () => updateUser(placeholder.id, { is_active: false }))}
          >
            Deactivate {placeholder.email}
          </Button>
        </div>
      ) : null}

      <Card as="form" onSubmit={submit} aria-label="Create user" className="flex flex-wrap items-end gap-3">
        <Input
          label="Email"
          type="email"
          controlClassName="w-64"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Select label="Role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
          {ROLES.map((option) => (
            <option key={option} value={option}>
              {ROLE_LABELS[option]}
            </option>
          ))}
        </Select>
        <Input
          label="Temporary password"
          type="text"
          controlClassName="w-72 font-mono"
          required
          minLength={MIN_PASSWORD_LENGTH}
          autoComplete="off"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <Button className="mb-0.5" onClick={() => setPassword(generateTemporaryPassword())}>
          Generate
        </Button>
        <Button type="submit" variant="primary" className="mb-0.5" disabled={busy}>
          Create user
        </Button>
      </Card>

      {created ? (
        <p data-testid="created-once" className="rounded-lg border border-ok bg-ok-tint p-3 text-base text-ink">
          Give <strong>{created.email}</strong> this temporary password once, in person or over a channel
          you trust: <code className="font-mono">{created.password}</code>. It is not stored anywhere
          readable and cannot be shown again.
        </p>
      ) : null}
      {reset ? (
        <p data-testid="reset-once" className="rounded-lg border border-ok bg-ok-tint p-3 text-base text-ink">
          New temporary password for <strong>{reset.email}</strong>:{" "}
          <code className="font-mono">{reset.password}</code>. Shown once.
        </p>
      ) : null}
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {resetting ? (
        <Dialog
          as="form"
          title={`Reset password for ${resetting.email}`}
          onSubmit={submitReset}
          onClose={() => setResetting(null)}
          footer={
            <>
              <Button onClick={() => setResetting(null)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={busy}>
                Set password
              </Button>
            </>
          }
        >
          <Input
            label="New temporary password"
            type="text"
            controlClassName="font-mono"
            required
            minLength={MIN_PASSWORD_LENGTH}
            autoComplete="off"
            value={resetPassword}
            onChange={(event) => setResetPassword(event.target.value)}
          />
          <Button className="self-start" onClick={() => setResetPassword(generateTemporaryPassword())}>
            Generate
          </Button>
        </Dialog>
      ) : null}

      <TableWrap>
        <Table minWidth="66rem">
          <THead>
            <tr>
              <Th>Email</Th>
              <Th>Role</Th>
              <Th>Active</Th>
              <Th>Last sign-in</Th>
              <Th>Locked or blocked</Th>
              <Th>Password</Th>
              <Th aria-label="Actions" />
            </tr>
          </THead>
          <TBody>
            {loading && !items.length ? <SkeletonTableRows rows={4} columns={7} /> : null}
            {!loading && !items.length ? (
              <tr>
                <td colSpan={7}>
                  <EmptyState title="No users yet." description="Create the first account above." />
                </td>
              </tr>
            ) : null}
            {items.map((item) => {
              const self = item.id === viewer?.id;
              return (
                <Tr key={item.id} data-testid="user-row">
                  <Td>
                    {item.email}
                    {self ? <span className="ml-1 text-sm text-ink-soft">(you)</span> : null}
                  </Td>
                  <Td>
                    <Select
                      label={`Role for ${item.email}`}
                      labelHidden
                      controlClassName="py-1"
                      value={item.role}
                      disabled={busy || self}
                      onChange={(event) =>
                        run(`Role changed for ${item.email}.`, () =>
                          updateUser(item.id, { role: event.target.value as Role }),
                        )
                      }
                    >
                      {ROLES.map((option) => (
                        <option key={option} value={option}>
                          {ROLE_LABELS[option]}
                        </option>
                      ))}
                    </Select>
                  </Td>
                  <Td>{item.is_active ? "active" : "deactivated"}</Td>
                  <Td className="text-sm text-ink-soft">
                    {item.last_login_at ? formatDateTime(item.last_login_at) : "never"}
                  </Td>
                  <Td data-testid="lock-state">
                    {item.locked || item.rate_limited ? (
                      <div className="flex flex-wrap gap-1">
                        {item.locked ? (
                          <Badge tone="risk">locked until {formatDateTime(item.locked_until)}</Badge>
                        ) : null}
                        {item.rate_limited ? (
                          <Badge
                            tone="warn"
                            title="Too many failed sign-ins from one address in 15 minutes. Unlock clears it."
                          >
                            Temporarily blocked (until {formatTime(item.rate_limited_until)})
                          </Badge>
                        ) : null}
                      </div>
                    ) : (
                      "—"
                    )}
                  </Td>
                  <Td className="text-sm">{item.must_change_password ? "must change" : "set"}</Td>
                  <Td>
                    <div className="flex flex-wrap justify-end gap-1">
                      {item.locked || item.rate_limited ? (
                        <Button
                          size="sm"
                          disabled={busy}
                          title="Clears the account lock and every failed-attempt counter for this email"
                          onClick={() => run(`${item.email} unlocked.`, () => unlockUser(item.id))}
                        >
                          Unlock
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={() => {
                          setResetting(item);
                          setResetPassword("");
                        }}
                      >
                        Reset password
                      </Button>
                      {item.is_active ? (
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={busy || self}
                          onClick={() => run(`${item.email} deactivated.`, () => updateUser(item.id, { is_active: false }))}
                        >
                          Deactivate
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() => run(`${item.email} reactivated.`, () => updateUser(item.id, { is_active: true }))}
                        >
                          Reactivate
                        </Button>
                      )}
                    </div>
                  </Td>
                </Tr>
              );
            })}
          </TBody>
        </Table>
      </TableWrap>
    </div>
  );
}
