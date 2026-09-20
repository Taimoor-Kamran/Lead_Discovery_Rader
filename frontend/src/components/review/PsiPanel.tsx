import { psiLines, type PsiRating } from "@/lib/format";

const RATING_STYLE: Record<PsiRating, string> = {
  good: "border-teal-300 bg-teal-50 text-teal-700",
  "needs work": "border-amber-300 bg-amber-50 text-amber-900",
  poor: "border-red-300 bg-red-50 text-red-800",
};

/** PageSpeed as three readable lines, each with Google's own good / needs work / poor band. */
export function PsiPanel({ psi }: { psi: Record<string, unknown> | null | undefined }) {
  const lines = psiLines(psi);
  if (!lines.length) return <p className="text-xs text-slate-500">No PageSpeed measurement.</p>;
  return (
    <dl className="flex flex-col gap-1 text-sm" data-testid="psi">
      {lines.map((line) => (
        <div key={line.key} className="flex flex-wrap items-center gap-2" data-testid="psi-line">
          <dt className="w-36 text-slate-600">{line.label}</dt>
          <dd className="font-mono">{line.value}</dd>
          {line.rating ? <dd className={`chip ${RATING_STYLE[line.rating]}`}>{line.rating}</dd> : null}
        </div>
      ))}
    </dl>
  );
}
