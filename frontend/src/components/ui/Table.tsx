"use client";

import { cx } from "./cx";

export type SortDirection = "ascending" | "descending";

/**
 * The scroll container. A table that does not fit scrolls **inside here**; the page itself
 * never scrolls sideways, at any width.
 */
export function TableWrap({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cx("overflow-x-auto rounded-lg border border-line bg-surface", className)}>{children}</div>
  );
}

export function Table({
  minWidth = "60rem",
  className,
  children,
  ...rest
}: React.TableHTMLAttributes<HTMLTableElement> & { minWidth?: string }) {
  return (
    <table className={cx("w-full text-base", className)} style={{ minWidth }} {...rest}>
      {children}
    </table>
  );
}

export function THead({ children }: { children: React.ReactNode }) {
  return <thead className="border-b border-line bg-surface-sunken text-left text-sm text-ink-soft">{children}</thead>;
}

export function TBody({ children }: { children: React.ReactNode }) {
  return <tbody className="divide-y divide-line">{children}</tbody>;
}

export function Tr({
  className,
  selected = false,
  ...rest
}: React.HTMLAttributes<HTMLTableRowElement> & { selected?: boolean }) {
  return (
    <tr
      className={cx(
        "group align-top transition-colors",
        selected ? "bg-accent-tint" : "hover:bg-surface-sunken",
        className,
      )}
      {...rest}
    />
  );
}

export function Th({
  numeric = false,
  sort,
  onSort,
  className,
  children,
  ...rest
}: Omit<React.ThHTMLAttributes<HTMLTableCellElement>, "onSort"> & {
  /** Right-aligned tabular figures, for a column of numbers. */
  numeric?: boolean;
  /** `undefined` = not a sortable column; `"none"` = sortable but not the active one. */
  sort?: SortDirection | "none";
  onSort?: () => void;
}) {
  const base = cx("px-3 py-2 font-medium", numeric && "text-right tabular-nums", className);
  if (!sort || !onSort) {
    return (
      <th scope="col" className={base} {...rest}>
        {children}
      </th>
    );
  }
  return (
    <th scope="col" aria-sort={sort === "none" ? "none" : sort} className={base} {...rest}>
      <button
        type="button"
        onClick={onSort}
        className={cx(
          "inline-flex items-center gap-1 rounded font-medium hover:text-ink",
          numeric && "w-full justify-end",
          sort !== "none" && "text-ink",
        )}
      >
        {children}
        <span aria-hidden="true" className="text-xs">
          {sort === "ascending" ? "↑" : sort === "descending" ? "↓" : "↕"}
        </span>
      </button>
    </th>
  );
}

export function Td({
  numeric = false,
  className,
  ...rest
}: React.TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return <td className={cx("px-3 py-2", numeric && "text-right font-mono tabular-nums", className)} {...rest} />;
}
