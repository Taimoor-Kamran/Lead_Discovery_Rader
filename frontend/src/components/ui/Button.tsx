"use client";

import { forwardRef } from "react";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";
export type ButtonSize = "sm" | "md";

export type ButtonProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "className"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Mid-submit: disables the button, marks it busy and (optionally) changes the word. */
  loading?: boolean;
  loadingLabel?: string;
  className?: string;
};

const BASE =
  "inline-flex items-center justify-center gap-2 rounded border font-medium transition-colors " +
  "disabled:cursor-not-allowed disabled:opacity-60";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "border-accent bg-accent text-surface hover:enabled:border-accent-strong hover:enabled:bg-accent-strong",
  secondary: "border-line bg-surface text-ink hover:enabled:bg-surface-sunken",
  danger: "border-risk bg-surface text-risk hover:enabled:bg-risk-tint",
  ghost: "border-transparent bg-transparent text-ink-soft hover:enabled:bg-surface-sunken hover:enabled:text-ink",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "px-2.5 py-1 text-sm",
  md: "px-3 py-1.5 text-base",
};

/**
 * The only button in the app. A button says what happens ("Approve"), and the flow keeps
 * the word ("approved") — see docs/design.md.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading = false, loadingLabel, className, children, disabled, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(BASE, VARIANTS[variant], SIZES[size], className)}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {loading && loadingLabel ? loadingLabel : children}
    </button>
  );
});

/** Only ever inside a button that is answering a click; stopped by `prefers-reduced-motion`. */
function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  );
}
