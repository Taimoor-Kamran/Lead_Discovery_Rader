import { SafeLink } from "@/components/SafeLink";
import { findingLabel, severityLabel } from "@/lib/labels";

export type Finding = {
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

/** Every finding in plain words, with the audit's own sentence and its evidence. */
export function FindingList({ findings }: { findings: Finding[] }) {
  if (!findings.length) return <p className="text-sm text-slate-600">No findings.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {findings.map((finding, index) => (
        <li
          key={`${finding.code}-${index}`}
          className="rounded border border-slate-200 p-2 text-sm"
          data-testid="finding"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip ${SEVERITY_STYLE[finding.severity ?? ""] ?? ""}`}>
              {severityLabel(finding.severity)}
            </span>
            <span className="font-medium" title={finding.code}>
              {findingLabel(finding.code)}
            </span>
          </div>
          {finding.message ? <p className="mt-1 text-slate-700">{finding.message}</p> : null}
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
  );
}
