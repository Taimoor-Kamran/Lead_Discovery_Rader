import { cx } from "./cx";

export type BadgeTone = "neutral" | "accent" | "ok" | "warn" | "risk";

const TONES: Record<BadgeTone, string> = {
  neutral: "border-line bg-surface-sunken text-ink",
  accent: "border-accent bg-accent-tint text-accent",
  ok: "border-ok bg-ok-tint text-ok",
  warn: "border-warn bg-warn-tint text-warn",
  risk: "border-risk bg-risk-tint text-risk",
};

/**
 * A status word. Colour never carries the meaning on its own — the word is always there
 * too, so the badge reads the same in greyscale, in print and to a screen reader.
 */
export function Badge({
  tone = "neutral",
  className,
  ...rest
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
      {...rest}
    />
  );
}
