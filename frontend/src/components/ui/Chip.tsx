import { cx } from "./cx";

/**
 * One item among many — a service, a finding, a platform. Quieter than a Badge, which
 * carries a status; a Chip carries a name and may be interactive.
 */
export function Chip({
  className,
  as: Tag = "span",
  interactive = false,
  ...rest
}: React.HTMLAttributes<HTMLElement> & { as?: "span" | "label" | "div"; interactive?: boolean }) {
  return (
    <Tag
      className={cx(
        "inline-flex items-center gap-1.5 rounded border border-line bg-surface px-2 py-0.5 text-sm text-ink",
        interactive && "cursor-pointer transition-colors hover:border-accent hover:bg-accent-tint",
        className,
      )}
      {...rest}
    />
  );
}
