"use client";

import { forwardRef, useId } from "react";
import { cx } from "./cx";

/** The shared shape of every text-like control, so they cannot drift apart. */
export const CONTROL =
  "rounded border border-line bg-surface px-2.5 py-1.5 text-base text-ink " +
  "placeholder:text-ink-soft disabled:cursor-not-allowed disabled:bg-surface-sunken " +
  "aria-[invalid=true]:border-risk";

export type FieldShell = {
  /** The visible label. Kept free of hints and markers so it is also the accessible name. */
  label: React.ReactNode;
  hint?: React.ReactNode;
  error?: React.ReactNode;
  /** Layout class for the wrapper; `className` on the control itself is `controlClassName`. */
  className?: string;
  controlClassName?: string;
};

/** Label, control, hint and error, wired together with ids. Used by every control below. */
export function Field({
  id,
  label,
  hint,
  error,
  className,
  children,
  inline = false,
}: FieldShell & { id: string; children: React.ReactNode; inline?: boolean }) {
  return (
    <div className={cx(inline ? "flex items-center gap-2" : "flex flex-col gap-1", className)}>
      <label htmlFor={id} className={cx("text-sm font-medium text-ink-soft", inline && "order-2")}>
        {label}
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-ink-soft">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs text-risk">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function describedBy(id: string, hint: unknown, error: unknown): string | undefined {
  return cx(hint ? `${id}-hint` : "", error ? `${id}-error` : "") || undefined;
}

export type InputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "className"> & FieldShell;

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, className, controlClassName, id: given, ...rest },
  ref,
) {
  const auto = useId();
  const id = given ?? auto;
  return (
    <Field id={id} label={label} hint={hint} error={error} className={className}>
      <input
        id={id}
        ref={ref}
        aria-describedby={describedBy(id, hint, error)}
        aria-invalid={error ? true : undefined}
        className={cx(CONTROL, controlClassName)}
        {...rest}
      />
    </Field>
  );
});

export type TextareaProps = Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, "className"> & FieldShell;

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, hint, error, className, controlClassName, id: given, ...rest },
  ref,
) {
  const auto = useId();
  const id = given ?? auto;
  return (
    <Field id={id} label={label} hint={hint} error={error} className={className}>
      <textarea
        id={id}
        ref={ref}
        aria-describedby={describedBy(id, hint, error)}
        aria-invalid={error ? true : undefined}
        className={cx(CONTROL, controlClassName)}
        {...rest}
      />
    </Field>
  );
});

export type SelectProps = Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "className"> & FieldShell;

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, error, className, controlClassName, id: given, children, ...rest },
  ref,
) {
  const auto = useId();
  const id = given ?? auto;
  return (
    <Field id={id} label={label} hint={hint} error={error} className={className}>
      <select
        id={id}
        ref={ref}
        aria-describedby={describedBy(id, hint, error)}
        aria-invalid={error ? true : undefined}
        className={cx(CONTROL, controlClassName)}
        {...rest}
      >
        {children}
      </select>
    </Field>
  );
});

export type CheckboxProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "className" | "type"> &
  Omit<FieldShell, "hint" | "error">;

/** A checkbox sits before its label, so the two are one target. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, className, controlClassName, id: given, ...rest },
  ref,
) {
  const auto = useId();
  const id = given ?? auto;
  return (
    <div className={cx("flex items-center gap-2", className)}>
      <input
        id={id}
        ref={ref}
        type="checkbox"
        className={cx("h-4 w-4 shrink-0 rounded-sm border-line accent-accent", controlClassName)}
        {...rest}
      />
      <label htmlFor={id} className="text-base text-ink">
        {label}
      </label>
    </div>
  );
});
