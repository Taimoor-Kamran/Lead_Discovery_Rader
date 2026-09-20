import type { ReviewDetail } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { SafeLink } from "@/components/SafeLink";

type Finding = {
  code?: string;
  severity?: string;
  message?: string;
  evidence_text?: string | null;
  evidence_url?: string | null;
};

const SEVERITY_STYLE: Record<string, string> = {
  high: "border-red-300 bg-red-50 text-red-800",
  medium: "border-amber-300 bg-amber-50 text-amber-900",
  low: "border-slate-300 bg-slate-100 text-slate-800",
  info: "border-slate-200 bg-white text-slate-600",
};

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
        <span className="text-xs text-slate-500">
          {audit.status} · {formatDateTime(audit.created_at)} · {audit.rules_version}
        </span>
      </header>
      <p className="text-xs text-slate-600">
        Audited <SafeLink href={audit.url_audited}>{audit.url_audited}</SafeLink>
        {audit.http_status ? ` · HTTP ${audit.http_status}` : ""}
      </p>

      <h3 className="text-sm font-medium text-slate-700">Findings ({findings.length})</h3>
      {findings.length ? (
        <ul className="flex flex-col gap-2">
          {findings.map((finding, index) => (
            <li
              key={`${finding.code}-${index}`}
              className="rounded border border-slate-200 p-2 text-sm"
              data-testid="finding"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className={`chip ${SEVERITY_STYLE[finding.severity ?? ""] ?? ""}`}>
                  {finding.severity ?? "unknown"}
                </span>
                <span className="font-mono text-xs">{finding.code}</span>
              </div>
              {finding.message ? <p className="mt-1">{finding.message}</p> : null}
              {finding.evidence_text ? (
                <blockquote className="mt-1 border-l-2 border-slate-300 pl-2 text-xs text-slate-700">
                  {finding.evidence_text}
                </blockquote>
              ) : null}
              {finding.evidence_url ? (
                <p className="mt-1 text-xs">
                  Evidence: <SafeLink href={finding.evidence_url} />
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-600">No findings.</p>
      )}

      <h3 className="text-sm font-medium text-slate-700">PageSpeed</h3>
      {Object.keys(psi).length ? (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          {Object.entries(psi).map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-slate-500">{key}</dt>
              <dd className="font-mono">{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-xs text-slate-500">No PageSpeed measurement.</p>
      )}

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
