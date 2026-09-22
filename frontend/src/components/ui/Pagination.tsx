"use client";

import { Button } from "./Button";

/**
 * The row under a table: how much is on screen, and one way to get more. The count is
 * always shown, so "load more" never leaves a reviewer guessing how far they are.
 */
export function Pagination({
  count,
  noun,
  loading = false,
  hasMore = false,
  onLoadMore,
}: {
  count: number;
  /** Singular and plural, e.g. `["business", "businesses"]`. */
  noun: [string, string];
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
}) {
  return (
    <div className="flex items-center gap-3 text-sm text-ink-soft">
      <span aria-live="polite">
        {loading ? "Loading…" : `${count} ${count === 1 ? noun[0] : noun[1]}`}
      </span>
      {hasMore && onLoadMore ? (
        <Button size="sm" onClick={onLoadMore} disabled={loading}>
          Load more
        </Button>
      ) : null}
    </div>
  );
}
