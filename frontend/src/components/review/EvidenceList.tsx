import { AiLabel } from "@/components/AiLabel";
import { SafeLink } from "@/components/SafeLink";
import { findingLabel } from "@/lib/labels";

export type Evidence = {
  finding_code?: string | null;
  text?: string | null;
  url?: string | null;
  source?: string;
};

export type EvidenceGroup = {
  key: string;
  finding_code: string | null;
  text: string | null;
  url: string | null;
  fromRules: boolean;
  fromAi: boolean;
  /** The model's quote when it differs from the rules' evidence for the same finding. */
  aiQuote: string | null;
};

/**
 * One entry per finding. When the rules and the model cite the same finding, they become
 * one item that says the AI agrees, rather than the same line twice.
 */
export function groupEvidence(items: Evidence[]): EvidenceGroup[] {
  type Parts = { rules: Evidence | null; ai: Evidence | null };
  const parts = new Map<string, Parts>();
  for (const item of items) {
    const key = item.finding_code ?? `text:${item.text ?? ""}|${item.url ?? ""}`;
    const entry = parts.get(key) ?? { rules: null, ai: null };
    if (item.source === "ai") entry.ai = entry.ai ?? item;
    else entry.rules = entry.rules ?? item;
    parts.set(key, entry);
  }
  return [...parts.entries()].map(([key, { rules, ai }]) => {
    // The rules' evidence is the stored fact; the model's quote only adds when it differs.
    const text = rules?.text ?? ai?.text ?? null;
    const aiText = ai?.text ?? null;
    return {
      key,
      finding_code: rules?.finding_code ?? ai?.finding_code ?? null,
      text,
      url: rules?.url ?? ai?.url ?? null,
      fromRules: rules !== null,
      fromAi: ai !== null,
      aiQuote: rules && ai && aiText && aiText !== text ? aiText : null,
    };
  });
}

export function EvidenceList({ items }: { items: Evidence[] }) {
  const groups = groupEvidence(items);
  if (!groups.length) return <p className="text-sm text-slate-600">No evidence recorded.</p>;
  return (
    <ul className="mt-1 flex flex-col gap-1 text-sm">
      {groups.map((group) => (
        <li key={group.key} className="rounded border border-slate-100 bg-slate-50 p-2" data-testid="evidence">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
            <span className="font-medium text-slate-800" title={group.finding_code ?? undefined}>
              {group.finding_code ? findingLabel(group.finding_code) : "Observation"}
            </span>
            {group.fromRules && group.fromAi ? (
              <span className="chip border-teal-300 bg-teal-50 text-teal-700" data-testid="ai-agrees">
                AI agrees
              </span>
            ) : null}
            {group.fromAi && !group.fromRules ? <AiLabel /> : null}
          </div>
          {group.text ? <blockquote className="mt-1 whitespace-pre-wrap">{group.text}</blockquote> : null}
          {group.aiQuote ? (
            <p className="mt-1 whitespace-pre-wrap text-xs text-slate-600">
              <span className="font-medium text-amber-800">AI quote:</span> {group.aiQuote}
            </p>
          ) : null}
          {group.url ? (
            <p className="mt-1 text-xs">
              Source: <SafeLink href={group.url} />
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
