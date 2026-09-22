"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import {
  Badge,
  Button,
  Card,
  Chip,
  EmptyState,
  PageHeader,
  SkeletonLines,
  useToast,
} from "@/components/ui";
import { AiSummaryBox } from "@/components/review/AiSummaryBox";
import { AuditPanel } from "@/components/review/AuditPanel";
import { BusinessFacts } from "@/components/review/BusinessFacts";
import { DecisionDialog, type Assignee, type DecisionFields } from "@/components/review/DecisionDialog";
import { OPEN_STATUSES, OpportunityCard, opportunityAnchor } from "@/components/review/OpportunityCard";
import { ShortcutsHelp } from "@/components/review/ShortcutsHelp";
import { SourceProvenance } from "@/components/review/SourceProvenance";
import { useShortcuts } from "@/hooks/useShortcuts";
import {
  ApiError,
  getReviewDetail,
  getReviewQueue,
  getSalesReps,
  reviewOpportunity,
  undoDecision,
  type Decision,
  type ReviewDecisionRead,
  type ReviewDetail,
  type ReviewOpportunity,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CONFLICT_MESSAGE, QUEUE_ORDER_KEY } from "@/lib/review";
import { loadFailed } from "@/lib/errors";
import { place, score } from "@/lib/format";
import { serviceLabel } from "@/lib/labels";
import { canDecide, canPickAssignee } from "@/lib/roles";

export { CONFLICT_MESSAGE, QUEUE_ORDER_KEY };

type Pending = { decision: Decision; opportunity: ReviewOpportunity };

/**
 * The whole review screen for one business. Owns the fetching, the decision flow (dialog →
 * API → toast with Undo → next business), the 409 handling and the keyboard shortcuts.
 */
export function BusinessReview({ businessId }: { businessId: string }) {
  const router = useRouter();
  const { user } = useAuth();
  const { show } = useToast();
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(0);
  const [help, setHelp] = useState(false);
  const [assignees, setAssignees] = useState<Assignee[]>([]);
  const [order, setOrder] = useState<string[]>([]);
  const decider = user ? canDecide(user.role) : false;

  const load = useCallback(async () => {
    try {
      setDetail(await getReviewDetail(businessId));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : loadFailed("this business"));
    }
  }, [businessId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!user || !canPickAssignee(user.role)) return;
    getSalesReps()
      .then((page) => setAssignees(page.items.map((rep) => ({ id: rep.id, email: rep.email }))))
      .catch(() => setAssignees([]));
  }, [user]);

  // The queue page wrote its order; without one, fall back to the default queue order.
  useEffect(() => {
    let stored: string[] = [];
    try {
      stored = JSON.parse(window.sessionStorage.getItem(QUEUE_ORDER_KEY) ?? "[]") as string[];
    } catch {
      stored = [];
    }
    if (stored.includes(businessId)) {
      setOrder(stored);
      return;
    }
    getReviewQueue({ limit: 200 })
      .then((page) => setOrder(page.items.map((item) => item.business_id)))
      .catch(() => setOrder([]));
  }, [businessId]);

  const position = order.indexOf(businessId);
  const nextId = position >= 0 ? order[position + 1] ?? null : null;
  const previousId = position > 0 ? order[position - 1] ?? null : null;
  const opportunities = detail?.opportunities ?? [];
  const openOpportunities = opportunities.filter((o) => OPEN_STATUSES.has(o.review_status));
  const openCount = openOpportunities.length;
  const focusedOpportunity = opportunities[Math.min(focused, Math.max(0, opportunities.length - 1))];

  function goNext() {
    if (nextId) router.push(`/review/${nextId}`);
    else router.push("/review");
  }

  function goPrevious() {
    if (previousId) router.push(`/review/${previousId}`);
  }

  function startDecision(decision: Decision, opportunity: ReviewOpportunity) {
    if (!decider || !OPEN_STATUSES.has(opportunity.review_status)) return;
    setPending({ decision, opportunity });
  }

  async function undo(decision: ReviewDecisionRead) {
    try {
      const result = await undoDecision(decision.id);
      show({
        tone: "success",
        message: `Undone. ${result.suppressions_lifted ? "The suppression was lifted. " : ""}`,
      });
      await load();
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Could not undo" });
      await load();
    }
  }

  async function submitDecision(fields: DecisionFields) {
    if (!pending) return;
    setBusy(true);
    try {
      const result = await reviewOpportunity(pending.opportunity.id, {
        decision: pending.decision,
        lock_version: pending.opportunity.lock_version,
        reason_code: fields.reason_code ?? null,
        note: fields.note ?? null,
        duplicate_of: fields.duplicate_of ?? null,
        assigned_to: fields.assigned_to ?? null,
      });
      setPending(null);
      const decisionRow = result.decision;
      show(
        {
          tone: "success",
          message: `${pending.opportunity.service_name}: ${labelFor(pending.decision)}.`,
          action: decisionRow.can_undo ? { label: "Undo", run: () => undo(decisionRow) } : undefined,
        },
        15000,
      );
      const remaining =
        pending.decision === "do_not_contact"
          ? 0
          : opportunities.filter(
              (o) => o.id !== pending.opportunity.id && OPEN_STATUSES.has(o.review_status),
            ).length;
      if (remaining === 0) goNext();
      else await load();
    } catch (caught) {
      setPending(null);
      if (caught instanceof ApiError && caught.status === 409) {
        show({ tone: "error", message: CONFLICT_MESSAGE }, 12000);
        setError(null);
      } else {
        show({
          tone: "error",
          message: caught instanceof ApiError ? caught.message : "The decision did not go through",
        });
      }
      await load();
    } finally {
      setBusy(false);
    }
  }

  const shortcuts = useMemo(
    () => ({
      j: goNext,
      k: goPrevious,
      a: () => focusedOpportunity && startDecision("approve", focusedOpportunity),
      r: () => focusedOpportunity && startDecision("reject", focusedOpportunity),
      "?": () => setHelp(true),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nextId, previousId, focusedOpportunity, decider],
  );
  useShortcuts(shortcuts, !pending && !help);

  if (error) {
    return (
      <ErrorNote>
        {error}{" "}
        <Link href="/review" className="rounded font-medium text-accent underline underline-offset-2">
          Back to the queue
        </Link>
      </ErrorNote>
    );
  }
  if (!detail) {
    // The shape of the page, so nothing jumps when the business arrives.
    return (
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.55fr)_minmax(21rem,1fr)]">
        <SkeletonLines lines={8} />
        <SkeletonLines lines={5} />
      </div>
    );
  }

  const dncTarget = opportunities.find((o) => OPEN_STATUSES.has(o.review_status));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        meta={
          <Link href="/review" className="rounded font-medium text-accent underline underline-offset-2">
            ← Queue
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {detail.business.display_name}
            <span className="text-base font-normal text-ink-soft">
              {place(detail.business.city, detail.business.state)}
            </span>
            {detail.suppressed ? <Badge tone="risk">Do not contact</Badge> : null}
          </span>
        }
        description={`${openCount} open opportunit${openCount === 1 ? "y" : "ies"} to decide on.`}
        actions={
          <>
            <Button onClick={goPrevious} disabled={!previousId}>
              ← Previous
            </Button>
            <Button onClick={goNext}>Next →</Button>
            <Button variant="ghost" onClick={() => setHelp(true)} aria-label="Keyboard shortcuts">
              ?
            </Button>
            {decider ? (
              <Button
                variant="danger"
                disabled={!dncTarget || detail.suppressed || busy}
                title={
                  detail.suppressed
                    ? "Already suppressed"
                    : dncTarget
                      ? undefined
                      : "No open opportunity to decide on; an admin can suppress the business directly"
                }
                onClick={() => dncTarget && setPending({ decision: "do_not_contact", opportunity: dncTarget })}
              >
                Do not contact
              </Button>
            ) : null}
          </>
        }
      />

      {/* Two columns, not three panels: the case on the left, the decision on the right. */}
      <div
        data-testid="review-columns"
        className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.55fr)_minmax(21rem,1fr)]"
      >
        <div className="order-2 flex min-w-0 flex-col gap-5 lg:order-1">
          <BusinessFacts detail={detail} />
          <SourceProvenance sources={detail.sources} linkedProfiles={detail.linked_profiles} />
          <AuditPanel detail={detail} />
          {/* Rules-only is a supported configuration, so with AI off there is no empty box
              where an AI answer would have gone (spec v0.10.0 §3). Only an explicit `false`
              hides it: a response that does not carry the flag must not hide what the model
              did say. */}
          {detail.ai_enabled === false ? null : <AiSummaryBox ai={detail.ai} />}
        </div>

        <div className="order-1 flex min-w-0 flex-col gap-3 lg:order-2 lg:sticky lg:top-4 lg:self-start">
          <h2 className="text-md font-semibold text-ink">
            Opportunities <span className="font-normal text-ink-soft">({opportunities.length})</span>
          </h2>

          {openOpportunities.length ? (
            <nav
              aria-label="Open opportunities"
              className="flex flex-wrap items-center gap-2"
              data-testid="open-summary"
            >
              {openOpportunities.map((opportunity) => (
                <a key={opportunity.id} href={`#${opportunityAnchor(opportunity.id)}`} className="rounded">
                  <Chip interactive title={`${opportunity.service} · jump to this opportunity`}>
                    <span className="font-medium">{serviceLabel(opportunity.service)}</span>
                    <span className="font-mono text-xs text-ink-soft">Score {score(opportunity.score)}</span>
                  </Chip>
                </a>
              ))}
            </nav>
          ) : null}

          {opportunities.length ? (
            opportunities.map((opportunity, index) => (
              <OpportunityCard
                key={opportunity.id}
                opportunity={opportunity}
                aiEnabled={detail.ai_enabled !== false}
                focused={index === focused}
                canDecide={decider && !detail.suppressed}
                busy={busy}
                onFocus={() => setFocused(index)}
                onDecide={(decision) => startDecision(decision, opportunity)}
                onUndo={undo}
              />
            ))
          ) : (
            <Card className="border-dashed">
              <EmptyState
                title="No opportunities for this business."
                description="Nothing was found to sell here. It will come back if a later audit finds something."
              />
            </Card>
          )}
        </div>
      </div>

      {pending ? (
        <DecisionDialog
          decision={pending.decision}
          service={pending.opportunity.service}
          excludeOpportunityId={pending.opportunity.id}
          assignees={assignees}
          busy={busy}
          onSubmit={submitDecision}
          onCancel={() => setPending(null)}
        />
      ) : null}
      {help ? <ShortcutsHelp onClose={() => setHelp(false)} /> : null}
    </div>
  );
}

function labelFor(decision: Decision): string {
  switch (decision) {
    case "approve":
      return "approved";
    case "reject":
      return "rejected";
    case "needs_enrichment":
      return "marked needs enrichment";
    case "duplicate":
      return "marked duplicate";
    case "not_a_fit":
      return "marked not a fit";
    case "do_not_contact":
      return "do not contact";
  }
}
