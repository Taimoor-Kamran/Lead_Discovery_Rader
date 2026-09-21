import { Badge, type BadgeTone } from "@/components/ui";
import { psiLines, type PsiRating } from "@/lib/format";

const RATING_TONE: Record<PsiRating, BadgeTone> = {
  good: "ok",
  "needs work": "warn",
  poor: "risk",
};

/** PageSpeed as labelled lines, each with Google's own good / needs work / poor band. */
export function PsiPanel({ psi }: { psi: Record<string, unknown> | null | undefined }) {
  const lines = psiLines(psi);
  if (!lines.length) return <p className="text-sm text-ink-soft">No PageSpeed measurement.</p>;
  return (
    <dl className="flex flex-col gap-1.5 text-base" data-testid="psi">
      {lines.map((line) => (
        <div key={line.key} className="flex flex-wrap items-center gap-2" data-testid="psi-line">
          <dt className="w-40 text-ink-soft">{line.label}</dt>
          <dd className="font-mono tabular-nums">{line.value}</dd>
          {line.rating ? (
            <dd>
              <Badge tone={RATING_TONE[line.rating]}>{line.rating}</Badge>
            </dd>
          ) : null}
        </div>
      ))}
    </dl>
  );
}
