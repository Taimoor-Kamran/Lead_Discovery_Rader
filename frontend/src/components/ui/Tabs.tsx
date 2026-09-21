"use client";

import { useRef } from "react";
import { cx } from "./cx";

export type TabOption<T extends string> = { value: T; label: React.ReactNode };

/**
 * A tab list with the keyboard behaviour people expect: arrow keys move between tabs and
 * select as they go, Home and End jump to the ends, and only the selected tab is in the
 * tab order.
 */
export function Tabs<T extends string>({
  label,
  value,
  options,
  onChange,
  panelId,
  className,
}: {
  label: string;
  value: T;
  options: readonly TabOption<T>[];
  onChange: (value: T) => void;
  /** The region the tabs control, so the two are linked for a screen reader. */
  panelId?: string;
  className?: string;
}) {
  const list = useRef<HTMLDivElement | null>(null);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const index = options.findIndex((option) => option.value === value);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? options.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + options.length) % options.length;
    onChange(options[next].value);
    list.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }

  return (
    <div
      ref={list}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cx("flex gap-1 border-b border-line", className)}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            id={`tab-${option.value}`}
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(option.value)}
            className={cx(
              "-mb-px border-b-2 px-3 py-2 text-base transition-colors",
              selected
                ? "border-accent font-medium text-ink"
                : "border-transparent text-ink-soft hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
