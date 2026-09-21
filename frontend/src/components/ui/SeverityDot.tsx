import { cx } from "./cx";

export type Severity = "high" | "medium" | "low" | "info";

const TONES: Record<Severity, string> = {
  high: "bg-risk",
  medium: "bg-warn",
  low: "bg-ink-soft",
  info: "bg-line",
};

/**
 * The marker in a findings list. Decorative on purpose: it is always followed by the
 * severity word, so the list never relies on colour to say how bad something is.
 */
export function SeverityDot({ severity, className }: { severity: string | null | undefined; className?: string }) {
  const tone = TONES[(severity ?? "info") as Severity] ?? TONES.info;
  return <span aria-hidden="true" className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", tone, className)} />;
}
