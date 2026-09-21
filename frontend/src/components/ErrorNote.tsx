import { cx } from "@/components/ui";

/**
 * Something went wrong on this screen. The message always names the cause and the fix
 * (see src/lib/errors.ts); the note is an alert so it is announced when it appears.
 */
export function ErrorNote({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      role="alert"
      className={cx("rounded-lg border border-risk bg-risk-tint p-3 text-base text-ink", className)}
    >
      {children}
    </p>
  );
}
