import { SafeLink } from "@/components/SafeLink";
import { SeverityDot } from "@/components/ui";
import { findingLabel, severityLabel } from "@/lib/labels";

export type Finding = {
  code?: string;
  severity?: string;
  message?: string;
  evidence_text?: string | null;
  evidence_url?: string | null;
};

const ORDER: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

/**
 * What the audit found, as one list ranked by severity — not six stacked cards. A card
 * each would say the six findings matter equally; the dot and the severity word rank
 * them, and the quoted evidence underneath is what a reviewer actually weighs.
 */
export function FindingList({ findings }: { findings: Finding[] }) {
  if (!findings.length) return <p className="text-base text-ink-soft">No findings.</p>;
  const ranked = [...findings].sort(
    (a, b) => (ORDER[a.severity ?? "info"] ?? 9) - (ORDER[b.severity ?? "info"] ?? 9),
  );
  return (
    <ul className="divide-y divide-line">
      {ranked.map((finding, index) => (
        <li key={`${finding.code}-${index}`} className="flex gap-3 py-3 first:pt-0 last:pb-0" data-testid="finding">
          <SeverityDot severity={finding.severity} />
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium text-ink" title={finding.code}>
                {findingLabel(finding.code)}
              </span>
              <span className="text-sm text-ink-soft">{severityLabel(finding.severity)}</span>
            </p>
            {finding.message ? <p className="mt-0.5 max-w-measure text-base text-ink-soft">{finding.message}</p> : null}
            {finding.evidence_text ? (
              <blockquote className="mt-1 border-l-2 border-line pl-2 font-mono text-sm text-ink">
                {finding.evidence_text}
              </blockquote>
            ) : null}
            {finding.evidence_url ? (
              <p className="mt-1 text-sm text-ink-soft">
                Evidence: <SafeLink href={finding.evidence_url} />
              </p>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}
