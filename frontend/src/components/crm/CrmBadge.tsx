"use client";

import { SafeLink } from "@/components/SafeLink";
import type { CrmLeadBlock } from "@/lib/api";
import { CRM_STATUS_LABELS, formatDateTime } from "@/lib/format";

const STYLE: Record<string, string> = {
  scheduled: "border-slate-300 bg-slate-50 text-slate-700",
  syncing: "border-slate-300 bg-slate-50 text-slate-700",
  synced: "border-teal-600 bg-teal-50 text-teal-700",
  held: "border-amber-400 bg-amber-50 text-amber-900",
  cancelled: "border-slate-300 bg-slate-100 text-slate-600",
  withdrawn: "border-slate-300 bg-slate-100 text-slate-600",
};

type Props = {
  crm: CrmLeadBlock | null | undefined;
  canRetry?: boolean;
  busy?: boolean;
  onRetry?: (crmLeadId: string) => void;
};

/**
 * Where a lead's CRM record stands: Scheduled (with the time), In CRM ✓ (linking to the
 * record when the CRM has a link), Held ⚠ (with Retry for CRM managers and admins).
 */
export function CrmBadge({ crm, canRetry = false, busy = false, onRetry }: Props) {
  if (!crm) {
    return (
      <span className="chip border-slate-200 bg-white text-slate-500" data-testid="crm-badge" data-status="none">
        Not scheduled
      </span>
    );
  }
  const label = CRM_STATUS_LABELS[crm.status] ?? crm.status;
  const detail =
    crm.status === "scheduled"
      ? ` · ${formatDateTime(crm.due_at)}`
      : crm.status === "synced" || crm.status === "withdrawn"
        ? ` · ${formatDateTime(crm.last_synced_at)}`
        : "";
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid="crm-badge" data-status={crm.status}>
      <span
        className={`chip ${STYLE[crm.status] ?? ""}`}
        title={crm.status === "held" && crm.last_error ? crm.last_error : `CRM status: ${crm.status}`}
      >
        {crm.status === "synced" ? "In CRM ✓" : crm.status === "held" ? "Held ⚠" : label}
        {detail ? <span className="font-normal text-slate-600">{detail}</span> : null}
      </span>
      {crm.status === "synced" && crm.external_url ? (
        <SafeLink href={crm.external_url} className="text-xs">
          Open record
        </SafeLink>
      ) : null}
      {crm.status === "held" && canRetry && onRetry ? (
        <button type="button" className="btn-secondary !py-0.5 text-xs" disabled={busy} onClick={() => onRetry(crm.id)}>
          Retry
        </button>
      ) : null}
    </span>
  );
}
