import type { ReviewDetail } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { auditStatusLabel } from "@/lib/labels";
import { SafeLink } from "@/components/SafeLink";
import { FindingList, type Finding } from "@/components/review/FindingList";
import { PsiPanel } from "@/components/review/PsiPanel";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** Middle column: what the audit saw, every finding with its evidence, PSI, tech stack. */
export function AuditPanel({ detail }: { detail: ReviewDetail }) {
  const audit = detail.audit;
  if (!audit) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-600">
        <h2 className="text-lg font-semibold text-navy">Website audit</h2>
        <p className="mt-2">This business has not been audited yet.</p>
      </section>
    );
  }
  const findings = (audit.findings as Finding[]) ?? [];
  const psi = asRecord(audit.psi);
  const tech = asRecord(audit.tech_stack);
  const platforms = Array.isArray(tech.platforms) ? (tech.platforms as unknown[]) : [];

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <header className="flex items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-navy">Website audit</h2>
        <span className="text-xs text-slate-500" title={`${audit.status} · ${audit.rules_version}`}>
          {auditStatusLabel(audit.status)} · {formatDateTime(audit.created_at)}
        </span>
      </header>
      <p className="text-xs text-slate-600">
        Audited <SafeLink href={audit.url_audited}>{audit.url_audited}</SafeLink>
        {audit.http_status ? ` · HTTP ${audit.http_status}` : ""}
      </p>

      <h3 className="text-sm font-medium text-slate-700">Findings ({findings.length})</h3>
      <FindingList findings={findings} />

      <h3 className="text-sm font-medium text-slate-700">PageSpeed</h3>
      <PsiPanel psi={psi} />

      <h3 className="text-sm font-medium text-slate-700">Tech stack</h3>
      {platforms.length ? (
        <ul className="flex flex-wrap gap-1">
          {platforms.map((platform, index) => (
            <li key={index} className="chip border-slate-200 bg-slate-50 text-slate-700">
              {typeof platform === "string" ? platform : JSON.stringify(platform)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500">No platform detected.</p>
      )}
    </section>
  );
}
