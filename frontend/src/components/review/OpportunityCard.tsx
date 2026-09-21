"use client";

import { EvidenceList, type Evidence } from "@/components/review/EvidenceList";
import { ReasonLines } from "@/components/review/ReasonLines";
import { Badge, type BadgeTone, Button, Card, cx, Disclosure, Tooltip } from "@/components/ui";
import type { Decision, ReviewDecisionRead, ReviewOpportunity } from "@/lib/api";
import { DECISION_LABELS, formatDateTime, percent, REASON_LABELS, score, STATUS_LABELS } from "@/lib/format";
import { serviceLabel, sourceLabel } from "@/lib/labels";

const SOURCE_TONE: Record<string, BadgeTone> = {
  rules: "neutral",
  ai: "warn",
  "rules+ai": "accent",
};

const STATUS_TONE: Record<string, BadgeTone> = {
  pending: "neutral",
  needs_enrichment: "warn",
  approved: "ok",
  rejected: "neutral",
  duplicate: "neutral",
  not_a_fit: "neutral",
  do_not_contact: "risk",
};

const COMPONENTS: [keyof ReviewOpportunity["score_components"], string][] = [
  ["facts", "Facts"],
  ["inference", "Inference"],
  ["intent", "Intent"],
  ["contactability", "Contactability"],
];

export const OPEN_STATUSES = new Set(["pending", "needs_enrichment"]);

export function opportunityAnchor(id: string): string {
  return `opportunity-${id}`;
}

type Props = {
  opportunity: ReviewOpportunity;
  focused: boolean;
  canDecide: boolean;
  busy: boolean;
  onFocus: () => void;
  onDecide: (decision: Decision) => void;
  onUndo: (decision: ReviewDecisionRead) => void;
};

/**
 * One claim, in the decision column: what we would sell, what it scores, the two buttons
 * that settle it, and — underneath — why, with the evidence behind the why.
 */
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
  const showDecisions = canDecide && open;

  return (
    <Card
      as="article"
      padded={false}
      id={opportunityAnchor(opportunity.id)}
      tabIndex={0}
      onFocus={onFocus}
      onClick={onFocus}
      aria-current={focused ? "true" : undefined}
      data-testid="opportunity-card"
      className={cx(
        "scroll-mt-4 focus-ring print-break-avoid print-plain",
        focused ? "border-accent" : "border-line",
      )}
    >
      {/* Sticks to the top of the column while the card is in view, so the decision is
          always one click away however long the evidence below runs. */}
      <div
        className="sticky top-0 z-10 flex flex-col gap-2 rounded-t-lg border-b border-line bg-surface p-3"
        data-testid="card-top"
      >
        <header className="flex flex-wrap items-center gap-2">
          <h3 className="text-md font-semibold text-ink" title={opportunity.service}>
            {serviceLabel(opportunity.service)}
          </h3>
          <Tooltip content={`Confidence ${percent(opportunity.confidence)} · raw score ${opportunity.score}`}>
            <span
              className="ml-auto inline-flex items-baseline gap-1 font-mono text-md font-medium tabular-nums text-ink"
              title={`Confidence ${percent(opportunity.confidence)} · raw score ${opportunity.score}`}
              data-testid="score-chip"
            >
              Score {score(opportunity.score)}
            </span>
          </Tooltip>
        </header>
        {showDecisions ? (
          <div className="flex flex-wrap gap-2" aria-label="Decisions">
            <Button variant="primary" disabled={busy} onClick={() => onDecide("approve")}>
              Approve
            </Button>
            <Button disabled={busy} onClick={() => onDecide("reject")}>
              Reject
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDecide("needs_enrichment")}>
              Needs enrichment
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDecide("duplicate")}>
              Duplicate
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDecide("not_a_fit")}>
              Not a fit
            </Button>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={STATUS_TONE[opportunity.review_status] ?? "neutral"}>
            {STATUS_LABELS[opportunity.review_status] ?? opportunity.review_status}
          </Badge>
          <Badge tone={SOURCE_TONE[opportunity.source] ?? "neutral"} title={`Where this claim came from: ${opportunity.source}`}>
            {sourceLabel(opportunity.source)}
          </Badge>
          {opportunity.weak ? <Badge tone="warn">weak signal</Badge> : null}
        </div>
      </div>

      <div className="flex flex-col gap-3 p-3">
        {/* The AI label sits on the AI rationale line (and on AI-only evidence), once per
            AI item — not as a banner over the whole card. */}
        <ReasonLines
          ruleReason={opportunity.rule_reason}
          aiRationale={opportunity.ai_rationale}
          fallback={opportunity.reason}
        />

        <div>
          <h4 className="mb-1 text-sm font-semibold text-ink">Evidence</h4>
          <EvidenceList items={evidence} />
        </div>

        <div className="grid grid-cols-[7rem_1fr_2.5rem] items-center gap-x-2 gap-y-1 text-sm" aria-label="Score components">
          {COMPONENTS.map(([key, label]) => {
            const value = opportunity.score_components[key];
            return (
              <div key={key} className="contents">
                <span className="text-ink-soft">{label}</span>
                <div className="h-1.5 rounded-full bg-surface-sunken">
                  <div
                    className="h-1.5 rounded-full bg-accent"
                    style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
                    role="img"
                    aria-label={`${label} ${percent(value)}`}
                  />
                </div>
                <span className="text-right font-mono tabular-nums">{score(value)}</span>
              </div>
            );
          })}
          <span className="font-medium text-ink">Total</span>
          <div className="h-1.5 rounded-full bg-surface-sunken">
            <div className="h-1.5 rounded-full bg-ink" style={{ width: `${opportunity.score * 100}%` }} />
          </div>
          <span className="text-right font-mono font-medium tabular-nums">{score(opportunity.score)}</span>
        </div>

        {opportunity.ai ? (
          <Disclosure summary="Details" testId="ai-details">
            <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-sm text-ink-soft">
              <dt>Model</dt>
              <dd className="font-mono">{opportunity.ai.model}</dd>
              <dt>Prompt</dt>
              <dd className="font-mono">{opportunity.ai.prompt_version}</dd>
              <dt>Status</dt>
              <dd>
                {opportunity.ai.status}
                {opportunity.ai.escalated ? " — escalated" : ""}
              </dd>
              {opportunity.ai_agrees === false ? (
                <>
                  <dt>Agreement</dt>
                  <dd>The model did not name this service.</dd>
                </>
              ) : null}
              <dt>Confidence</dt>
              <dd>{percent(opportunity.confidence)}</dd>
            </dl>
          </Disclosure>
        ) : null}

        {opportunity.history.length ? (
          <div>
            <h4 className="mb-1 text-sm font-semibold text-ink">History</h4>
            <ul className="flex flex-col gap-2 text-sm">
              {opportunity.history.map((decision) => (
                <li key={decision.id} className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid="history-row">
                  <span className="font-medium">{DECISION_LABELS[decision.decision] ?? decision.decision}</span>
                  {decision.reason_code ? (
                    <span className="text-ink-soft">{REASON_LABELS[decision.reason_code] ?? decision.reason_code}</span>
                  ) : null}
                  {decision.assigned_to_email ? (
                    <span className="text-ink-soft">assigned to {decision.assigned_to_email}</span>
                  ) : null}
                  <span className="w-full text-ink-soft">
                    by {decision.decided_by_email ?? "unknown"} on {formatDateTime(decision.decided_at)}
                    {decision.undone_at ? `, undone ${formatDateTime(decision.undone_at)}` : ""}
                  </span>
                  {decision.note ? <span className="w-full">“{decision.note}”</span> : null}
                  {decision.can_undo ? (
                    <Button size="sm" disabled={busy} onClick={() => onUndo(decision)}>
                      Undo
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
