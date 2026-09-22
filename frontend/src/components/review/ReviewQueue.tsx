"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DecisionDialog, type DecisionFields } from "@/components/review/DecisionDialog";
import {
  EMPTY_FILTERS,
  isFiltered,
  QueueFilters,
  type QueueFilterState,
} from "@/components/review/QueueFilters";
import { QueueTable } from "@/components/review/QueueTable";
import { Button, EmptyState, PageHeader, Pagination, Tabs, useToast } from "@/components/ui";
import { ApiError, getReviewQueue, reviewBatch, type QueueItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { loadFailed } from "@/lib/errors";
import { QUEUE_ORDER_KEY } from "@/lib/review";
import { canDecide } from "@/lib/roles";
import { ErrorNote } from "@/components/ErrorNote";

const STATUS_TABS = [
  { value: "pending", label: "Pending" },
  { value: "needs_enrichment", label: "Needs enrichment" },
] as const;

const PANEL_ID = "queue-results";
const BATCH_LIMIT = 50;

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
        setError(caught instanceof ApiError ? caught.message : loadFailed("the review queue"));
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
        ids: selectedIds.slice(0, BATCH_LIMIT),
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

  const tooMany = selected.size > BATCH_LIMIT;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Review queue"
        description="Businesses with open opportunities, strongest first. Weak signals are hidden until you ask for them."
        actions={
          decider ? (
            <div className="flex flex-wrap items-center gap-2" data-testid="batch-bar">
              <span className="text-sm text-ink-soft" aria-live="polite">
                {selected.size} selected
              </span>
              <Button disabled={!selected.size || tooMany} onClick={() => setBatch("reject")}>
                Reject selected
              </Button>
              <Button disabled={!selected.size || tooMany} onClick={() => setBatch("not_a_fit")}>
                Not a fit selected
              </Button>
              {tooMany ? (
                <span className="text-sm text-risk">At most {BATCH_LIMIT} at a time.</span>
              ) : null}
            </div>
          ) : null
        }
      />

      <Tabs
        label="Status"
        value={filters.status}
        options={STATUS_TABS}
        panelId={PANEL_ID}
        onChange={(status) => setFilters({ ...filters, status })}
      />

      <QueueFilters value={filters} onChange={setFilters} />

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <div id={PANEL_ID} role="tabpanel" aria-labelledby={`tab-${filters.status}`} className="flex flex-col gap-4">
        <QueueTable
          items={items}
          selected={selected}
          onToggle={toggle}
          onToggleBusiness={toggleBusiness}
          canSelect={decider}
          showWeak={filters.include_weak}
          loading={loading}
          empty={
            isFiltered(filters) ? (
              <EmptyState
                title="Nothing to review with these filters."
                description="Widen the service, the city, the score or the search to see more."
                action={
                  <Button onClick={() => setFilters({ ...EMPTY_FILTERS, status: filters.status })}>
                    Clear the filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                title="Nothing to review."
                description="Run a search to find businesses."
                action={
                  <Link
                    href="/searches"
                    className="rounded font-medium text-accent underline underline-offset-2"
                  >
                    Go to Searches
                  </Link>
                }
              />
            )
          }
        />

        <Pagination
          count={items.length}
          noun={["business", "businesses"]}
          loading={loading}
          hasMore={Boolean(nextCursor)}
          onLoadMore={() => void load(nextCursor ?? undefined)}
        />
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
