"use client";

import { useEffect, useId, useRef } from "react";
import { cx } from "./cx";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A modal dialog: focus moves in on open, Tab is trapped inside, Esc closes it, and focus
 * returns to whatever opened it. The heading names the dialog, so its accessible name is
 * the title a reviewer reads.
 */
export function Dialog({
  title,
  onClose,
  children,
  footer,
  size = "md",
  as: Tag = "div",
  className,
  ...rest
}: {
  title: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md";
  /** `form` lets a dialog submit itself; everything else stays the same. */
  as?: "div" | "form";
  className?: string;
} & Omit<React.HTMLAttributes<HTMLElement>, "title">) {
  const panel = useRef<HTMLElement | null>(null);
  const headingId = useId();

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const node = panel.current;
    const focusables = () => Array.from(node?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    (focusables()[0] ?? node)?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !node?.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="print-hide fixed inset-0 z-40 flex items-center justify-center bg-scrim p-4">
      <Tag
        ref={panel as never}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        tabIndex={-1}
        className={cx(
          "flex w-full flex-col gap-3 rounded-lg border border-line bg-surface p-5 shadow-overlay",
          size === "sm" ? "max-w-sm" : "max-w-md",
          className,
        )}
        {...rest}
      >
        <h2 id={headingId} className="text-md font-semibold text-ink">
          {title}
        </h2>
        {children}
        {footer ? <div className="flex flex-wrap justify-end gap-2">{footer}</div> : null}
      </Tag>
    </div>
  );
}
