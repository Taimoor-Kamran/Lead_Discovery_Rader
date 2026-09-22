import { AiLabel } from "@/components/AiLabel";
import { cx } from "@/components/ui";

type Props = {
  ruleReason: string | null | undefined;
  aiRationale: string | null | undefined;
  /** What to show when neither part is known: the stored reason as one line. */
  fallback?: string | null;
  compact?: boolean;
};

/**
 * The rules' wording and the model's rationale on two labelled lines. Classification
 * stores them as one string; the API hands them back apart, and neither is reworded.
 */
export function ReasonLines({ ruleReason, aiRationale, fallback, compact = false }: Props) {
  const rule = ruleReason ?? (aiRationale ? null : (fallback ?? null));
  if (!rule && !aiRationale) return <p className="text-base text-ink-soft">No reason recorded.</p>;
  return (
    <dl className={cx("flex flex-col gap-1.5", compact ? "text-sm" : "text-base")} data-testid="reason-lines">
      {rule ? (
        <div className="flex gap-2">
          <dt className="shrink-0 font-medium text-ink-soft">Rules</dt>
          <dd className="max-w-measure" data-testid="rule-reason">
            {rule}
          </dd>
        </div>
      ) : null}
      {aiRationale ? (
        <div className="flex gap-2">
          <dt className="shrink-0 font-medium text-warn">AI</dt>
          <dd className="max-w-measure" data-testid="ai-rationale">
            {aiRationale} {compact ? null : <AiLabel />}
          </dd>
        </div>
      ) : null}
    </dl>
  );
}
