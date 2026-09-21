"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useToast } from "@/components/ui";
import { DecisionDialog, type DecisionFields } from "@/components/review/DecisionDialog";
import { EMPTY_FILTERS, QueueFilters, type QueueFilterState } from "@/components/review/QueueFilters";
import { QueueTable } from "@/components/review/QueueTable";
import { ApiError, getReviewQueue, reviewBatch, type QueueItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { QUEUE_ORDER_KEY } from "@/lib/review";
import { canDecide } from "@/lib/roles";

/** The queue page. Selection enables batch reject / not-a-fit only; nothing else is batched. */
export function ReviewQueue() {
  const { user } = useAuth();
  const { show } = useToast();
  const params = useSearchParams();
  const [filters, setFilters] = useState<QueueFilterState>(() => ({
    ...EMPTY_FILTERS,
    city: params.get("city") ?? "",
  }));
  const [items, setItems] = useState<QueueItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batch, setBatch] = useState<"reject" | "not_a_fit" | null>(null);
  const [busy, setBusy] = useState(false);
  const decider = user ? canDecide(user.role) : false;

  const load = useCallback(
    async (cursor?: string) => {
      setLoading(true);
      setError(null);
      try {
        const page = await getReviewQueue({
          status: filters.status,
          service: filters.service || undefined,
          city: filters.city.trim() || undefined,
          min_score: filters.min_score ? Number(filters.min_score) : undefined,
          q: filters.q.trim() || undefined,
          include_weak: filters.include_weak,
          cursor,
        });
        setItems((current) => (cursor ? [...current, ...page.items] : page.items));
        setNextCursor(page.next_cursor ?? null);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "Could not load the queue");
      } finally {
        setLoading(false);
      }
    },
    [filters],
  );

  useEffect(() => {
    const handle = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(handle);
  }, [load]);

  // The detail page walks this order with j/k and "next business".
  useEffect(() => {
    try {
      window.sessionStorage.setItem(
        QUEUE_ORDER_KEY,
        JSON.stringify(items.map((item) => item.business_id)),
      );
    } catch {
      // Private mode or storage disabled: next/previous falls back to the API order.
    }
  }, [items]);

  const selectedIds = useMemo(() => [...selected], [selected]);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleBusiness(item: QueueItem) {
    setSelected((current) => {
      const next = new Set(current);
      const all = item.opportunities.every((o) => next.has(o.id));
      for (const opportunity of item.opportunities) {
        if (all) next.delete(opportunity.id);
        else next.add(opportunity.id);
      }
      return next;
    });
  }

  async function submitBatch(fields: DecisionFields) {
    if (!batch) return;
    setBusy(true);
    try {
      const result = await reviewBatch({
        ids: selectedIds.slice(0, 50),
        decision: batch,
        reason_code: fields.reason_code ?? "",
        note: fields.note ?? null,
      });
      const conflicts = result.items.filter((item) => item.result !== "ok").length;
      show({
        tone: conflicts ? "info" : "success",
        message: conflicts
          ? `${result.decided} decided; ${conflicts} skipped (already decided elsewhere).`
          : `${result.decided} opportunit${result.decided === 1 ? "y" : "ies"} marked ${
              batch === "reject" ? "rejected" : "not a fit"
            }.`,
      });
      setSelected(new Set());
      setBatch(null);
      await load();
    } catch (caught) {
      show({
        tone: "error",
        message: caught instanceof ApiError ? caught.message : "The batch did not go through",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-navy">Review queue</h1>
          <p className="text-sm text-slate-600">
            Businesses with open opportunities, strongest first. Weak signals are hidden until
            you ask for them.
          </p>
        </div>
        {decider ? (
          <div className="flex items-center gap-2" data-testid="batch-bar">
            <span className="text-sm text-slate-600">{selected.size} selected</span>
            <button
              type="button"
              className="btn-secondary"
              disabled={!selected.size || selected.size > 50}
              onClick={() => setBatch("reject")}
            >
              Reject selected
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={!selected.size || selected.size > 50}
              onClick={() => setBatch("not_a_fit")}
            >
              Not a fit selected
            </button>
            {selected.size > 50 ? (
              <span className="text-xs text-red-700">At most 50 at a time.</span>
            ) : null}
          </div>
        ) : null}
      </header>

      <QueueFilters value={filters} onChange={setFilters} />

      {error ? (
        <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      ) : null}

      <QueueTable
        items={items}
        selected={selected}
        onToggle={toggle}
        onToggleBusiness={toggleBusiness}
        canSelect={decider}
        showWeak={filters.include_weak}
      />

      <div className="flex items-center gap-3 text-sm text-slate-600">
        {loading ? <span>Loading…</span> : <span>{items.length} business{items.length === 1 ? "" : "es"}</span>}
        {nextCursor ? (
          <button type="button" className="btn-secondary" onClick={() => load(nextCursor)}>
            Load more
          </button>
        ) : null}
      </div>

      {batch ? (
        <DecisionDialog
          decision={batch}
          count={selected.size}
          busy={busy}
          onSubmit={submitBatch}
          onCancel={() => setBatch(null)}
        />
      ) : null}
    </div>
  );
}
