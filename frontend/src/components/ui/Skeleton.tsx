import { cx } from "./cx";

/**
 * The shape of what is coming, not a spinner: a skeleton keeps the layout from jumping
 * when the data lands. `prefers-reduced-motion` flattens the pulse to a plain tint.
 */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cx("block animate-pulse rounded bg-surface-sunken", className)} />;
}

/** Rows of skeleton cells matching a table's real row height, inside the same wrapper. */
export function SkeletonRows({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className="flex flex-col gap-px" data-testid="skeleton-rows">
      {Array.from({ length: rows }).map((_, row) => (
        <div key={row} className="flex items-center gap-4 border-b border-line px-3 py-3 last:border-b-0">
          {Array.from({ length: columns }).map((_, column) => (
            <Skeleton key={column} className={cx("h-4", column === 0 ? "w-1/4" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** A block of lines, for a detail panel that has not arrived yet. */
export function SkeletonLines({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-2", className)} data-testid="skeleton-lines">
      {Array.from({ length: lines }).map((_, line) => (
        <Skeleton key={line} className={cx("h-4", line === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

/** The same idea inside a table, where only rows and cells are valid children. */
export function SkeletonTableRows({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, row) => (
        <tr key={row} data-testid="skeleton-row">
          {Array.from({ length: columns }).map((_, column) => (
            <td key={column} className="px-3 py-3">
              <Skeleton className={cx("h-4", column === 0 ? "w-40" : "w-full")} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
