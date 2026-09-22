"use client";

import { SafeLink } from "@/components/SafeLink";
import { Badge, type BadgeTone, Button } from "@/components/ui";
import type { CrmLeadBlock } from "@/lib/api";
import { CRM_STATUS_LABELS, formatDateTime } from "@/lib/format";

const TONE: Record<string, BadgeTone> = {
  scheduled: "neutral",
  syncing: "neutral",
  synced: "ok",
  held: "warn",
  cancelled: "neutral",
  withdrawn: "neutral",
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
      <span data-testid="crm-badge" data-status="none">
        <Badge>Not scheduled</Badge>
      </span>
    );
  }
  const label = CRM_STATUS_LABELS[crm.status] ?? crm.status;
  const when =
    crm.status === "scheduled"
      ? formatDateTime(crm.due_at)
      : crm.status === "synced" || crm.status === "withdrawn"
        ? formatDateTime(crm.last_synced_at)
        : "";
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" data-testid="crm-badge" data-status={crm.status}>
      <Badge
        tone={TONE[crm.status] ?? "neutral"}
        title={crm.status === "held" && crm.last_error ? crm.last_error : `CRM status: ${crm.status}`}
      >
        {crm.status === "synced" ? "In CRM ✓" : crm.status === "held" ? "Held ⚠" : label}
      </Badge>
      {when ? <span className="text-sm text-ink-soft">{when}</span> : null}
      {crm.status === "synced" && crm.external_url ? (
        <SafeLink href={crm.external_url} className="text-sm">
          Open record
        </SafeLink>
      ) : null}
      {crm.status === "held" && canRetry && onRetry ? (
        <Button size="sm" disabled={busy} onClick={() => onRetry(crm.id)}>
          Retry
        </Button>
      ) : null}
    </span>
  );
}
