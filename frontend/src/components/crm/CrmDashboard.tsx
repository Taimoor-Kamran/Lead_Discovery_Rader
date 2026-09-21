"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/ui";
import { SafeLink } from "@/components/SafeLink";
import {
  ApiError,
  downloadCrmExport,
  getCrmLeads,
  getCrmStatus,
  retryCrmLead,
  syncAllCrm,
  syncBusinessNow,
  type CrmLead,
  type CrmStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CRM_STATUS_LABELS, formatDateTime, place } from "@/lib/format";
import { canManageCrm } from "@/lib/roles";

/** Saves a blob through the browser. Replaceable so tests can watch it. */
export let saveFile: (filename: string, blob: Blob) => void = (filename, blob) => {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
};

export function setSaveFile(handler: typeof saveFile): void {
  saveFile = handler;
}

const COUNT_ORDER = ["scheduled", "syncing", "synced", "held", "withdrawn", "cancelled"];

/**
 * The CRM page: destination and health (what `make crm-check` prints), counts, the held
 * list with errors and Retry, the scheduled list, Export CSV and Sync all due.
 */
export function CrmDashboard() {
  const { user } = useAuth();
  const { show } = useToast();
  const manager = user ? canManageCrm(user.role) : false;
  const [status, setStatus] = useState<CrmStatus | null>(null);
  const [held, setHeld] = useState<CrmLead[]>([]);
  const [scheduled, setScheduled] = useState<CrmLead[]>([]);
  const [synced, setSynced] = useState<CrmLead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [statusRead, heldPage, scheduledPage, syncedPage] = await Promise.all([
        getCrmStatus(),
        manager ? getCrmLeads("held") : Promise.resolve({ items: [], next_cursor: null }),
        manager ? getCrmLeads("scheduled") : Promise.resolve({ items: [], next_cursor: null }),
        manager ? getCrmLeads("synced") : Promise.resolve({ items: [], next_cursor: null }),
      ]);
      setStatus(statusRead);
      setHeld(heldPage.items);
      setScheduled(scheduledPage.items);
      setSynced(syncedPage.items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the CRM status");
    }
  }, [manager]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(label: string, action: () => Promise<string>) {
    setBusy(true);
    try {
      const message = await action();
      show({ tone: "success", message });
      await load();
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : `${label} failed` });
    } finally {
      setBusy(false);
    }
  }

  const retry = (lead: CrmLead) =>
    run("Retry", async () => {
      const result = await retryCrmLead(lead.id);
      return `${lead.business_name}: ${CRM_STATUS_LABELS[result.status] ?? result.status}.`;
    });

  const sendNow = (lead: CrmLead) =>
    run("Send now", async () => {
      const result = await syncBusinessNow(lead.business_id);
      return `${lead.business_name}: ${CRM_STATUS_LABELS[result.status] ?? result.status}.`;
    });

  const syncAll = () =>
    run("Sync all", async () => {
      const result = await syncAllCrm();
      return `Considered ${result.considered}: ${result.synced} synced, ${result.held} held, ${result.scheduled} still waiting.`;
    });

  const exportCsv = (scope: "new" | "all") =>
    run("Export", async () => {
      const file = await downloadCrmExport(scope);
      saveFile(file.filename, file.blob);
      return `Exported ${file.filename}.`;
    });

  const isCsv = status?.destination === "csv";

  return (
    <div className="flex flex-col gap-4" data-testid="crm-dashboard">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-navy">CRM</h1>
          <p className="text-sm text-slate-600">
            Approved leads leave here — and only approved leads — once their undo window has closed.
            {manager ? "" : " Read-only for your role."}
          </p>
        </div>
        {manager ? (
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" disabled={busy} onClick={syncAll}>
              Sync all due
            </button>
            {isCsv ? (
              <>
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => exportCsv("new")}>
                  Export CSV (new)
                </button>
                <button type="button" className="btn-secondary" disabled={busy} onClick={() => exportCsv("all")}>
                  Export CSV (all)
                </button>
              </>
            ) : null}
          </div>
        ) : null}
      </header>
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}

      {status ? (
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_2fr]" aria-label="Destination">
          <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm" data-testid="crm-destination">
            <h2 className="text-lg font-semibold text-navy">
              Destination: {status.destination}
              {status.demo ? " (demo)" : ""}
            </h2>
            <p className={`mt-1 ${status.health.ok ? "text-teal-700" : "text-amber-900"}`} data-testid="crm-health">
              {status.health.ok ? "Healthy" : "Needs attention"}
              {status.health.message ? ` · ${status.health.message}` : ""}
            </p>
            <p className="mt-1 text-xs text-slate-600">
              Auto-sync {status.auto_sync ? "on" : "off"} · waits {status.sync_delay_minutes} min after an approval
            </p>
            <dl className="mt-3 grid grid-cols-[8rem_1fr] gap-y-0.5 text-xs" data-testid="crm-counts">
              {COUNT_ORDER.map((key) => (
                <div key={key} className="contents">
                  <dt className="text-slate-500">{CRM_STATUS_LABELS[key] ?? key}</dt>
                  <dd className="font-mono">{status.counts[key] ?? 0}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
            <h2 className="text-base font-semibold text-navy">Checks</h2>
            <p className="text-xs text-slate-600">The same table <code>make crm-check</code> prints.</p>
            <ul className="mt-2 max-h-72 overflow-auto text-xs" data-testid="crm-checks">
              {status.health.checks.map((check) => (
                <li key={check.name} className="flex gap-2 py-0.5">
                  <span className={check.ok ? "text-teal-700" : "text-amber-900"} aria-label={check.ok ? "ok" : "problem"}>
                    {check.ok ? "OK" : "!!"}
                  </span>
                  <span className="font-medium">{check.name}</span>
                  <span className="text-slate-600">{check.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      ) : null}

      {manager ? (
        <>
          <LeadTable
            title="Held"
            testId="crm-held"
            empty="Nothing is held."
            items={held}
            busy={busy}
            action={(lead) => (
              <button type="button" className="btn-secondary !py-0.5" disabled={busy} onClick={() => retry(lead)}>
                Retry
              </button>
            )}
          />
          <LeadTable
            title="Scheduled"
            testId="crm-scheduled"
            empty="Nothing is waiting."
            items={scheduled}
            busy={busy}
            action={(lead) => (
              <button
                type="button"
                className="btn-secondary !py-0.5"
                disabled={busy}
                title="Skips the undo-window wait, never the human gate"
                onClick={() => sendNow(lead)}
              >
                Send now
              </button>
            )}
          />
          <LeadTable title="In CRM" testId="crm-synced" empty="Nothing has been sent yet." items={synced} busy={busy} />
        </>
      ) : null}
    </div>
  );
}

function LeadTable({
  title,
  testId,
  empty,
  items,
  action,
}: {
  title: string;
  testId: string;
  empty: string;
  items: CrmLead[];
  busy: boolean;
  action?: (lead: CrmLead) => React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white" aria-label={title} data-testid={testId}>
      <h2 className="border-b border-slate-100 px-4 py-2 text-base font-semibold text-navy">
        {title} <span className="font-normal text-slate-500">({items.length})</span>
      </h2>
      {items.length ? (
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th scope="col" className="px-3 py-2">Business</th>
              <th scope="col" className="px-3 py-2">Services</th>
              <th scope="col" className="px-3 py-2">Status</th>
              <th scope="col" className="px-3 py-2">When</th>
              <th scope="col" className="px-3 py-2">Attempts</th>
              <th scope="col" className="px-3 py-2">Last error</th>
              <th scope="col" className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((lead) => (
              <tr key={lead.id} className="align-top" data-testid="crm-row">
                <td className="px-3 py-2">
                  <Link href={`/review/${lead.business_id}`} className="font-medium text-navy hover:underline">
                    {lead.business_name}
                  </Link>
                  <div className="text-xs text-slate-600">{place(lead.city, lead.state)}</div>
                </td>
                <td className="px-3 py-2 text-xs">{lead.services.join("; ") || "—"}</td>
                <td className="px-3 py-2">
                  {CRM_STATUS_LABELS[lead.status] ?? lead.status}
                  {lead.external_url ? (
                    <>
                      {" · "}
                      <SafeLink href={lead.external_url} className="text-xs">Open record</SafeLink>
                    </>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-xs text-slate-600">
                  {lead.status === "scheduled" ? `due ${formatDateTime(lead.due_at)}` : formatDateTime(lead.last_synced_at ?? lead.updated_at)}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{lead.attempts}</td>
                <td className="max-w-md px-3 py-2 text-xs text-amber-900" data-testid="crm-error">{lead.last_error ?? ""}</td>
                <td className="px-3 py-2 text-right">{action ? action(lead) : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="p-6 text-center text-sm text-slate-600">{empty}</p>
      )}
    </section>
  );
}
