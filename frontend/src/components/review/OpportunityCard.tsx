"use client";

import type { Decision, ReviewDecisionRead, ReviewOpportunity } from "@/lib/api";
import { DECISION_LABELS, formatDateTime, percent, REASON_LABELS, score, STATUS_LABELS } from "@/lib/format";
import { AiLabel } from "@/components/AiLabel";
import { SafeLink } from "@/components/SafeLink";

type Evidence = {
  finding_code?: string;
  text?: string | null;
  url?: string | null;
  source?: string;
};

const SOURCE_STYLE: Record<string, string> = {
  rules: "border-slate-300 bg-slate-100 text-slate-800",
  ai: "border-amber-300 bg-amber-50 text-amber-900",
  "rules+ai": "border-teal-300 bg-teal-50 text-teal-700",
};

const STATUS_STYLE: Record<string, string> = {
  pending: "border-slate-300 bg-white text-slate-700",
  needs_enrichment: "border-amber-300 bg-amber-50 text-amber-900",
  approved: "border-teal-600 bg-teal-50 text-teal-700",
  rejected: "border-slate-300 bg-slate-100 text-slate-600",
  duplicate: "border-slate-300 bg-slate-100 text-slate-600",
  not_a_fit: "border-slate-300 bg-slate-100 text-slate-600",
  do_not_contact: "border-red-300 bg-red-50 text-red-800",
};

const COMPONENTS: [keyof ReviewOpportunity["score_components"], string][] = [
  ["facts", "Facts"],
  ["inference", "Inference"],
  ["intent", "Intent"],
  ["contactability", "Contactability"],
];

export const OPEN_STATUSES = new Set(["pending", "needs_enrichment"]);

type Props = {
  opportunity: ReviewOpportunity;
  focused: boolean;
  canDecide: boolean;
  busy: boolean;
  onFocus: () => void;
  onDecide: (decision: Decision) => void;
  onUndo: (decision: ReviewDecisionRead) => void;
};

/** One claim: service, reason, evidence, four score bars, source badge, decisions, history. */
export function OpportunityCard({
  opportunity,
  focused,
  canDecide,
  busy,
  onFocus,
  onDecide,
  onUndo,
}: Props) {
  const open = OPEN_STATUSES.has(opportunity.review_status);
  const evidence = (opportunity.evidence as Evidence[]) ?? [];
  const aiInvolved = opportunity.source !== "rules";

  return (
    <article
      tabIndex={0}
      onFocus={onFocus}
      onClick={onFocus}
      aria-current={focused ? "true" : undefined}
      data-testid="opportunity-card"
      className={`flex flex-col gap-3 rounded-lg border bg-white p-4 ${
        focused ? "border-teal-600 ring-2 ring-teal-300" : "border-slate-200"
      }`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold text-navy">{opportunity.service_name}</h3>
        <span className={`chip ${SOURCE_STYLE[opportunity.source] ?? ""}`} title="Where this claim came from">
          {opportunity.source}
        </span>
        <span className={`chip ${STATUS_STYLE[opportunity.review_status] ?? ""}`}>
          {STATUS_LABELS[opportunity.review_status] ?? opportunity.review_status}
        </span>
        {opportunity.weak ? <span className="chip border-amber-300 bg-amber-50 text-amber-900">weak signal</span> : null}
        <span className="ml-auto font-mono text-sm">
          score {score(opportunity.score)} · confidence {percent(opportunity.confidence)}
        </span>
      </header>

      {aiInvolved ? <AiLabel /> : null}
      <p className="text-sm">{opportunity.reason}</p>

      <div className="grid grid-cols-[8rem_1fr_3rem] items-center gap-x-2 gap-y-1 text-xs" aria-label="Score components">
        {COMPONENTS.map(([key, label]) => {
          const value = opportunity.score_components[key];
          return (
            <div key={key} className="contents">
              <span className="text-slate-600">{label}</span>
              <div className="h-2 rounded bg-slate-100">
                <div
                  className="h-2 rounded bg-teal-600"
                  style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
                  role="img"
                  aria-label={`${label} ${percent(value)}`}
                />
              </div>
              <span className="text-right font-mono">{score(value)}</span>
            </div>
          );
        })}
        <span className="font-medium text-slate-700">Total</span>
        <div className="h-2 rounded bg-slate-100">
          <div className="h-2 rounded bg-navy" style={{ width: `${opportunity.score * 100}%` }} />
        </div>
        <span className="text-right font-mono font-medium">{score(opportunity.score)}</span>
      </div>

      <div>
        <h4 className="text-xs font-medium uppercase tracking-wide text-slate-500">Evidence</h4>
        {evidence.length ? (
          <ul className="mt-1 flex flex-col gap-1 text-sm">
            {evidence.map((item, index) => (
              <li key={index} className="rounded border border-slate-100 bg-slate-50 p-2" data-testid="evidence">
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                  <span className="font-mono">{item.finding_code ?? "—"}</span>
                  {item.source ? <span className="chip border-slate-200 bg-white">{item.source}</span> : null}
                  {item.source === "ai" ? <AiLabel /> : null}
                </div>
                {item.text ? <blockquote className="mt-1 whitespace-pre-wrap">{item.text}</blockquote> : null}
                {item.url ? (
                  <p className="mt-1 text-xs">
                    Source: <SafeLink href={item.url} />
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-600">No evidence recorded.</p>
        )}
      </div>

      {opportunity.ai ? (
        <p className="text-xs text-slate-600">
          AI provenance: {opportunity.ai.model} · {opportunity.ai.prompt_version} · {opportunity.ai.status}
          {opportunity.ai.escalated ? " · escalated" : ""}
          {opportunity.ai_agrees === false ? " · the model did not name this service" : ""}
        </p>
      ) : null}

      {canDecide && open ? (
        <div className="flex flex-wrap gap-2" aria-label="Decisions">
          <button type="button" className="btn-primary" disabled={busy} onClick={() => onDecide("approve")}>
            Approve
          </button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => onDecide("reject")}>
            Reject
          </button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => onDecide("needs_enrichment")}>
            Needs enrichment
          </button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => onDecide("duplicate")}>
            Duplicate
          </button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => onDecide("not_a_fit")}>
            Not a fit
          </button>
        </div>
      ) : null}

      {opportunity.history.length ? (
        <div>
          <h4 className="text-xs font-medium uppercase tracking-wide text-slate-500">History</h4>
          <ul className="mt-1 flex flex-col gap-1 text-xs">
            {opportunity.history.map((decision) => (
              <li key={decision.id} className="flex flex-wrap items-center gap-2" data-testid="history-row">
                <span className="font-medium">{DECISION_LABELS[decision.decision] ?? decision.decision}</span>
                {decision.reason_code ? <span>· {REASON_LABELS[decision.reason_code] ?? decision.reason_code}</span> : null}
                {decision.assigned_to_email ? <span>· assigned to {decision.assigned_to_email}</span> : null}
                <span className="text-slate-500">
                  by {decision.decided_by_email ?? "unknown"} · {formatDateTime(decision.decided_at)}
                </span>
                {decision.undone_at ? <span className="text-slate-500">· undone {formatDateTime(decision.undone_at)}</span> : null}
                {decision.note ? <span className="w-full text-slate-700">“{decision.note}”</span> : null}
                {decision.can_undo ? (
                  <button type="button" className="btn-secondary !py-0.5" disabled={busy} onClick={() => onUndo(decision)}>
                    Undo
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}
