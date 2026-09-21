"use client";

import { useId, useState } from "react";
import { cx } from "./cx";

/**
 * Detail a reviewer can ask for but does not need by default — field provenance, the
 * model's parameters. A real button with `aria-expanded`, not a `<details>`, so the open
 * state is ours to control and the trigger looks like every other control.
 */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  className,
  testId,
}: {
  /** A function gets the open state, for a trigger whose wording changes. */
  summary: React.ReactNode | ((open: boolean) => React.ReactNode);
  children: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  testId?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-2", className)} data-testid={testId}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex w-fit items-center gap-1.5 rounded text-sm font-medium text-accent hover:text-accent-strong"
      >
        <span aria-hidden="true" className="text-xs">
          {open ? "▾" : "▸"}
        </span>
        {typeof summary === "function" ? summary(open) : summary}
      </button>
      <div id={id} hidden={!open}>
        {open ? children : null}
      </div>
    </div>
  );
}
