import { AiLabel } from "@/components/AiLabel";
import { Card, Disclosure } from "@/components/ui";
import type { AISummary } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { aiStatusLabel, buyingIntentLabel, industryLabel } from "@/lib/labels";

/**
 * The model's summary — labelled, muted and last in the argument, because it is the only
 * part that is not a stored fact. Plain text only; nothing here is evidence.
 *
 * Every code the model speaks is read in words with the code in the tooltip, and every
 * multi-valued field is a list of elements rather than one joined string: a reader has to be
 * able to tell two unknowns apart, and a joined run-on hides where one ends.
 *
 * This whole component is only mounted when the AI layer is switched on. With
 * `AI_PROVIDER=disabled` the review page renders nothing here at all, rather than a box
 * saying an answer is missing.
 */
export function AiSummaryBox({ ai }: { ai: AISummary | null | undefined }) {
  if (!ai) {
    return (
      <Card className="print-hide border-dashed">
        <p className="text-base text-ink-soft">No AI classification for this business.</p>
      </Card>
    );
  }
  return (
    <Card className="print-hide bg-surface-sunken" aria-label="AI summary" data-testid="ai-summary">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <AiLabel />
        <span className="text-sm text-ink-soft">{formatDateTime(ai.created_at)}</span>
      </div>
      <p className="mt-2 max-w-measure whitespace-pre-wrap text-base text-ink-soft">
        {ai.business_summary ?? "No summary (expired or not produced)."}
      </p>
      <dl className="mt-3 grid grid-cols-[9rem_1fr] gap-y-1 text-sm text-ink-soft">
        <dt>Industry (AI)</dt>
        <dd title={ai.industry ?? undefined}>
          {industryLabel(ai.industry)}
          {ai.industry_matches_listing === false ? " — does not match the listing" : ""}
        </dd>
        <dt>Buying intent</dt>
        <dd title={ai.buying_intent ?? undefined} data-testid="buying-intent">
          {ai.buying_intent ? buyingIntentLabel(ai.buying_intent) : "unknown"}
        </dd>
        {ai.unknowns.length ? (
          <>
            <dt>Unknowns</dt>
            <dd>
              <ul className="flex list-disc flex-col gap-0.5 pl-4" data-testid="ai-unknowns">
                {ai.unknowns.map((unknown) => (
                  <li key={unknown} data-testid="ai-unknown">
                    {unknown}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        ) : null}
      </dl>
      <div className="mt-3">
        <Disclosure summary="Details" testId="ai-details">
          <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-sm text-ink-soft">
            <dt>Model</dt>
            <dd className="font-mono">{ai.model}</dd>
            <dt>Prompt</dt>
            <dd className="font-mono">{ai.prompt_version}</dd>
            <dt>Status</dt>
            <dd title={ai.status}>
              {aiStatusLabel(ai.status)}
              {ai.escalated ? " — escalated" : ""}
            </dd>
          </dl>
        </Disclosure>
      </div>
    </Card>
  );
}
