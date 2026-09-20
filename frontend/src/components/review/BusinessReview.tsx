"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useToast } from "@/components/Toast";
import { AiSummaryBox } from "@/components/review/AiSummaryBox";
import { AuditPanel } from "@/components/review/AuditPanel";
import { BusinessFacts } from "@/components/review/BusinessFacts";
import { DecisionDialog, type Assignee, type DecisionFields } from "@/components/review/DecisionDialog";
import { OPEN_STATUSES, OpportunityCard, opportunityAnchor } from "@/components/review/OpportunityCard";
import { ShortcutsHelp } from "@/components/review/ShortcutsHelp";
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
import { score } from "@/lib/format";
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
      setError(caught instanceof ApiError ? caught.message : "Could not load this business");
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
      <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
        {error} · <Link href="/review" className="underline">Back to the queue</Link>
      </p>
    );
  }
  if (!detail) return <p className="text-sm text-slate-600">Loading…</p>;

  const dncTarget = opportunities.find((o) => OPEN_STATUSES.has(o.review_status));

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <Link href="/review" className="text-sm text-teal-700 underline underline-offset-2">
          ← Queue
        </Link>
        <h1 className="text-xl font-semibold text-navy">{detail.business.display_name}</h1>
        <span className="text-sm text-slate-600">
          {openCount} open opportunit{openCount === 1 ? "y" : "ies"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button type="button" className="btn-secondary" onClick={goPrevious} disabled={!previousId}>
            ← Previous
          </button>
          <button type="button" className="btn-secondary" onClick={goNext}>
            Next →
          </button>
          <button type="button" className="btn-secondary" onClick={() => setHelp(true)} aria-label="Keyboard shortcuts">
            ?
          </button>
          {decider ? (
            <button
              type="button"
              className="btn-danger"
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
            </button>
          ) : null}
        </div>
      </header>

      {openOpportunities.length ? (
        <nav
          aria-label="Open opportunities"
          className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
          data-testid="open-summary"
        >
          <span className="text-slate-600">Open:</span>
          {openOpportunities.map((opportunity) => (
            <a
              key={opportunity.id}
              href={`#${opportunityAnchor(opportunity.id)}`}
              className="chip border-slate-300 bg-slate-50 text-navy hover:border-teal-600"
              title={`${opportunity.service} · jump to this opportunity`}
            >
              <span className="font-medium">{serviceLabel(opportunity.service)}</span>
              <span className="text-slate-600">Score {score(opportunity.score)}</span>
            </a>
          ))}
        </nav>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(18rem,1fr)_minmax(20rem,1.2fr)_minmax(24rem,1.6fr)]">
        <BusinessFacts detail={detail} />
        <AuditPanel detail={detail} />
        <div className="flex flex-col gap-3">
          <AiSummaryBox ai={detail.ai} />
          {opportunities.length ? (
            opportunities.map((opportunity, index) => (
              <OpportunityCard
                key={opportunity.id}
                opportunity={opportunity}
                focused={index === focused}
                canDecide={decider && !detail.suppressed}
                busy={busy}
                onFocus={() => setFocused(index)}
                onDecide={(decision) => startDecision(decision, opportunity)}
                onUndo={undo}
              />
            ))
          ) : (
            <p className="rounded border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-600">
              No opportunities for this business.
            </p>
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
