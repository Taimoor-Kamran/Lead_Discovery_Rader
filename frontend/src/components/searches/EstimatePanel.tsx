import { Card } from "@/components/ui";
import type { CostEstimate } from "@/lib/api";

/** The cost of a run before it starts, in the numbers the spec lists. */
export function EstimatePanel({ estimate }: { estimate: CostEstimate | null }) {
  if (!estimate) return <p className="text-base text-ink-soft">Estimating…</p>;
  const money = (value: number) => `$${value.toFixed(2)}`;
  return (
    <Card data-testid="estimate" as="div" className="bg-surface-sunken">
      <h3 className="mb-2 text-base font-semibold text-ink">
        Cost estimate for {estimate.max_results} results
      </h3>
      <dl className="grid grid-cols-1 gap-x-8 gap-y-1.5 text-base sm:grid-cols-2">
        <dt className="text-ink-soft">Google Places calls</dt>
        <dd data-testid="est-places">
          {estimate.uses_places ? (
            <span className="font-mono tabular-nums">≈ {estimate.places_calls}</span>
          ) : (
            "none (no Places source)"
          )}
          <span className="ml-2 text-sm text-ink-soft">
            {estimate.places_remaining_today} of {estimate.places_daily_cap} left today
          </span>
        </dd>
        <dt className="text-ink-soft">PageSpeed calls (at most)</dt>
        <dd data-testid="est-psi">
          <span className="font-mono tabular-nums">{estimate.pagespeed_calls}</span>
          <span className="ml-2 text-sm text-ink-soft">
            {estimate.pagespeed_remaining_today} of {estimate.pagespeed_daily_cap} left today
          </span>
        </dd>
        <dt className="text-ink-soft">AI calls (at most)</dt>
        <dd data-testid="est-ai">
          {estimate.ai_enabled ? (
            <>
              <span className="font-mono tabular-nums">{estimate.ai_calls}</span> via {estimate.ai_provider}
            </>
          ) : (
            "AI is disabled"
          )}
          <span className="ml-2 text-sm text-ink-soft">
            {money(estimate.ai_budget_remaining_usd)} of {money(estimate.ai_budget_usd)} budget left today
          </span>
        </dd>
      </dl>
      {estimate.blockers.length ? (
        <ul role="alert" data-testid="est-blockers" className="mt-3 list-disc pl-5 text-base text-risk">
          {estimate.blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
