"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import { SafeLink } from "@/components/SafeLink";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
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
import { loadFailed } from "@/lib/errors";
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
      setError(caught instanceof ApiError ? caught.message : loadFailed("the CRM status"));
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
    <div className="flex flex-col gap-5" data-testid="crm-dashboard">
      <PageHeader
        title="CRM"
        description={`Approved leads leave here — and only approved leads — once their undo window has closed.${manager ? "" : " Read-only for your role."}`}
        actions={
          manager ? (
            <>
              <Button variant="primary" disabled={busy} onClick={syncAll}>
                Sync all due
              </Button>
              {isCsv ? (
                <>
                  <Button disabled={busy} onClick={() => exportCsv("new")}>
                    Export CSV (new)
                  </Button>
                  <Button disabled={busy} onClick={() => exportCsv("all")}>
                    Export CSV (all)
                  </Button>
                </>
              ) : null}
            </>
          ) : null
        }
      />
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {status ? (
        <section className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_2fr]" aria-label="Destination">
          <Card data-testid="crm-destination">
            <h2 className="text-md font-semibold text-ink">
              Destination: {status.destination}
              {status.demo ? " (demo)" : ""}
            </h2>
            <p className="mt-2" data-testid="crm-health">
              <Badge tone={status.health.ok ? "ok" : "warn"}>
                {status.health.ok ? "Healthy" : "Needs attention"}
              </Badge>
              {status.health.message ? (
                <span className="ml-2 text-base text-ink-soft">{status.health.message}</span>
              ) : null}
            </p>
            <p className="mt-2 text-sm text-ink-soft">
              Auto-sync {status.auto_sync ? "on" : "off"}. Waits {status.sync_delay_minutes} minutes
              after an approval.
            </p>
            <dl className="mt-3 grid grid-cols-[9rem_1fr] gap-y-1 text-base" data-testid="crm-counts">
              {COUNT_ORDER.map((key) => (
                <div key={key} className="contents">
                  <dt className="text-ink-soft">{CRM_STATUS_LABELS[key] ?? key}</dt>
                  <dd className="font-mono tabular-nums">{status.counts[key] ?? 0}</dd>
                </div>
              ))}
            </dl>
          </Card>
          <Card>
            <h2 className="text-md font-semibold text-ink">Checks</h2>
            <p className="text-sm text-ink-soft">
              The same table <code className="font-mono">make crm-check</code> prints.
            </p>
            <ul className="mt-3 flex max-h-72 flex-col gap-1.5 overflow-auto text-sm" data-testid="crm-checks">
              {status.health.checks.map((check) => (
                <li key={check.name} className="flex flex-wrap items-baseline gap-2">
                  <Badge tone={check.ok ? "ok" : "warn"} aria-label={check.ok ? "ok" : "problem"}>
                    {check.ok ? "OK" : "Problem"}
                  </Badge>
                  <span className="font-medium">{check.name}</span>
                  <span className="text-ink-soft">{check.detail}</span>
                </li>
              ))}
            </ul>
          </Card>
        </section>
      ) : null}

      {manager ? (
        <>
          <LeadTable
            title="Held"
            testId="crm-held"
            empty="Nothing is held."
            emptyHint="A lead is held when the CRM refused it. Nothing is stuck."
            items={held}
            busy={busy}
            action={(lead) => (
              <Button size="sm" disabled={busy} onClick={() => retry(lead)}>
                Retry
              </Button>
            )}
          />
          <LeadTable
            title="Scheduled"
            testId="crm-scheduled"
            empty="Nothing waiting."
            emptyHint="Approved leads appear here 30 minutes after approval."
            items={scheduled}
            busy={busy}
            action={(lead) => (
              <Button
                size="sm"
                disabled={busy}
                title="Skips the undo-window wait, never the human gate"
                onClick={() => sendNow(lead)}
              >
                Send now
              </Button>
            )}
          />
          <LeadTable
            title="In CRM"
            testId="crm-synced"
            empty="Nothing has been sent yet."
            emptyHint="A lead lands here once its scheduled sync goes through."
            items={synced}
            busy={busy}
          />
        </>
      ) : null}
    </div>
  );
}

function LeadTable({
  title,
  testId,
  empty,
  emptyHint,
  items,
  action,
}: {
  title: string;
  testId: string;
  empty: string;
  emptyHint?: string;
  items: CrmLead[];
  busy: boolean;
  action?: (lead: CrmLead) => React.ReactNode;
}) {
  return (
    <section aria-label={title} data-testid={testId} className="flex flex-col gap-2">
      <h2 className="text-md font-semibold text-ink">
        {title} <span className="font-normal text-ink-soft">({items.length})</span>
      </h2>
      <TableWrap>
        <Table minWidth="60rem">
          <THead>
            <tr>
              <Th>Business</Th>
              <Th>Services</Th>
              <Th>Status</Th>
              <Th>When</Th>
              <Th numeric className="w-24">
                Attempts
              </Th>
              <Th>Last error</Th>
              <Th aria-label="Actions" />
            </tr>
          </THead>
          <TBody>
            {items.length ? (
              items.map((lead) => (
                <Tr key={lead.id} data-testid="crm-row">
                  <Td>
                    <Link
                      href={`/review/${lead.business_id}`}
                      className="rounded font-medium text-ink underline-offset-2 hover:text-accent hover:underline"
                    >
                      {lead.business_name}
                    </Link>
                    <div className="text-sm text-ink-soft">{place(lead.city, lead.state)}</div>
                  </Td>
                  <Td className="text-sm">{lead.services.join("; ") || "—"}</Td>
                  <Td>
                    {CRM_STATUS_LABELS[lead.status] ?? lead.status}
                    {lead.external_url ? (
                      <div>
                        <SafeLink href={lead.external_url} className="text-sm">
                          Open record
                        </SafeLink>
                      </div>
                    ) : null}
                  </Td>
                  <Td className="text-sm text-ink-soft">
                    {lead.status === "scheduled"
                      ? `due ${formatDateTime(lead.due_at)}`
                      : formatDateTime(lead.last_synced_at ?? lead.updated_at)}
                  </Td>
                  <Td numeric>{lead.attempts}</Td>
                  <Td className="max-w-md text-sm text-warn" data-testid="crm-error">
                    {lead.last_error ?? ""}
                  </Td>
                  <Td className="text-right">{action ? action(lead) : null}</Td>
                </Tr>
              ))
            ) : (
              <tr>
                <td colSpan={7}>
                  <EmptyState title={empty} description={emptyHint} />
                </td>
              </tr>
            )}
          </TBody>
        </Table>
      </TableWrap>
    </section>
  );
}
