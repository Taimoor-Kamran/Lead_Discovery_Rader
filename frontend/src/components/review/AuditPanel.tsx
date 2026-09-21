import { SafeLink } from "@/components/SafeLink";
import { Badge, Card, Chip } from "@/components/ui";
import { FindingList, type Finding } from "@/components/review/FindingList";
import { PsiPanel } from "@/components/review/PsiPanel";
import type { ReviewDetail } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { auditStatusLabel } from "@/lib/labels";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

const STATUS_TONE: Record<string, "neutral" | "ok" | "warn" | "risk"> = {
  done: "ok",
  skipped: "neutral",
  robots_blocked: "warn",
  unreachable: "risk",
  failed: "risk",
};

/**
 * The middle of the argument: what the audit found, ranked by severity, then how fast the
 * site is and what it is built with.
 */
export function AuditPanel({ detail }: { detail: ReviewDetail }) {
  const audit = detail.audit;
  if (!audit) {
    return (
      <Card className="print-break-avoid print-plain">
        <h2 className="text-md font-semibold text-ink">What the audit found</h2>
        <p className="mt-2 text-base text-ink-soft">This business has not been audited yet.</p>
      </Card>
    );
  }
  const findings = (audit.findings as Finding[]) ?? [];
  const psi = asRecord(audit.psi);
  const tech = asRecord(audit.tech_stack);
  const platforms = Array.isArray(tech.platforms) ? (tech.platforms as unknown[]) : [];

  return (
    <Card className="print-break-avoid print-plain">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-md font-semibold text-ink">
          What the audit found{" "}
          <span className="font-normal text-ink-soft">({findings.length})</span>
        </h2>
        <span className="flex items-center gap-2 text-sm text-ink-soft" title={`${audit.status} · ${audit.rules_version}`}>
          <Badge tone={STATUS_TONE[audit.status] ?? "neutral"}>{auditStatusLabel(audit.status)}</Badge>
          {formatDateTime(audit.created_at)}
        </span>
      </header>
      <p className="mt-1 text-sm text-ink-soft">
        Audited <SafeLink href={audit.url_audited}>{audit.url_audited}</SafeLink>
        {audit.http_status ? <span className="ml-2 font-mono">HTTP {audit.http_status}</span> : null}
      </p>

      <div className="mt-3">
        <FindingList findings={findings} />
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <h3 className="mb-2 text-base font-semibold text-ink">Speed</h3>
        <PsiPanel psi={psi} />
      </div>

      <div className="mt-4">
        <h3 className="mb-2 text-base font-semibold text-ink">Built with</h3>
        {platforms.length ? (
          <ul className="flex flex-wrap gap-1">
            {platforms.map((platform, index) => (
              <li key={index}>
                <Chip className="font-mono">
                  {typeof platform === "string" ? platform : JSON.stringify(platform)}
                </Chip>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-soft">No platform detected.</p>
        )}
      </div>
    </Card>
  );
}
