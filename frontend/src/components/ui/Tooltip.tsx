"use client";

import { useId, useState } from "react";
import { cx } from "./cx";

/**
 * A hint on hover **and** on focus, dismissed with Esc. It is never the only place a fact
 * lives — everything a reviewer must act on is in the page itself.
 */
export function Tooltip({
  content,
  children,
  className,
}: {
  content: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span
      className={cx("relative inline-flex", className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false);
      }}
    >
      <span aria-describedby={id}>{children}</span>
      <span
        role="tooltip"
        id={id}
        hidden={!open}
        className="absolute bottom-full left-1/2 z-30 mb-1 w-max max-w-xs -translate-x-1/2 rounded border border-line bg-surface px-2 py-1 text-xs text-ink shadow-overlay"
      >
        {content}
      </span>
    </span>
  );
}
