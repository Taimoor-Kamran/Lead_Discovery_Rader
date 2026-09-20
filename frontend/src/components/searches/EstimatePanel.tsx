import type { CostEstimate } from "@/lib/api";

/** The cost of a run before it starts, in the numbers the spec lists. */
export function EstimatePanel({ estimate }: { estimate: CostEstimate | null }) {
  if (!estimate) return <p className="text-sm text-slate-600">Estimating…</p>;
  const money = (value: number) => `$${value.toFixed(2)}`;
  return (
    <div data-testid="estimate" className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
      <h3 className="mb-2 font-semibold">Cost estimate for {estimate.max_results} results</h3>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
        <dt className="text-slate-600">Google Places calls</dt>
        <dd data-testid="est-places">
          {estimate.uses_places ? `≈ ${estimate.places_calls}` : "none (no Places source)"} ·{" "}
          {estimate.places_remaining_today} of {estimate.places_daily_cap} left today
        </dd>
        <dt className="text-slate-600">PageSpeed calls (at most)</dt>
        <dd data-testid="est-psi">
          {estimate.pagespeed_calls} · {estimate.pagespeed_remaining_today} of {estimate.pagespeed_daily_cap} left today
        </dd>
        <dt className="text-slate-600">AI calls (at most)</dt>
        <dd data-testid="est-ai">
          {estimate.ai_enabled ? `${estimate.ai_calls} via ${estimate.ai_provider}` : "AI is disabled"} ·{" "}
          {money(estimate.ai_budget_remaining_usd)} of {money(estimate.ai_budget_usd)} budget left today
        </dd>
      </dl>
      {estimate.blockers.length ? (
        <ul role="alert" data-testid="est-blockers" className="mt-2 list-disc pl-5 text-red-800">
          {estimate.blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
