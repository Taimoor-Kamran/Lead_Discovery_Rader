import { cx } from "./cx";

/**
 * The top of every screen: the one `h1`, one sentence about the screen, and the actions
 * that belong to the whole screen. No eyebrow above the heading — see docs/design.md.
 */
export function PageHeader({
  title,
  description,
  actions,
  meta,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Screen-level actions, right-aligned on a wide viewport. */
  actions?: React.ReactNode;
  /** A back link or breadcrumb, above the heading. */
  meta?: React.ReactNode;
  className?: string;
}) {
  return (
    <header className={cx("flex flex-wrap items-end justify-between gap-x-6 gap-y-3", className)}>
      <div className="min-w-0">
        {meta ? <div className="mb-1 text-sm">{meta}</div> : null}
        <h1 className="text-lg font-semibold text-ink">{title}</h1>
        {description ? <p className="mt-1 max-w-measure text-base text-ink-soft">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
