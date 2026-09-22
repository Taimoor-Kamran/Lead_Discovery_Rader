import { cx } from "./cx";

/**
 * Every screen answers "what do I do next" when it has nothing to show. That is what the
 * `action` slot is for; an empty state without one is a dead end.
 */
export function EmptyState({
  title,
  description,
  action,
  className,
  ...rest
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
} & Omit<React.HTMLAttributes<HTMLDivElement>, "title">) {
  return (
    <div className={cx("flex flex-col items-center gap-2 px-6 py-12 text-center", className)} {...rest}>
      <p className="text-md font-semibold text-ink">{title}</p>
      {description ? <p className="max-w-measure text-base text-ink-soft">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
