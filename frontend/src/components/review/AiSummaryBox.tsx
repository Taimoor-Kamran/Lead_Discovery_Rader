import type { AISummary } from "@/lib/api";
import { AiLabel } from "@/components/AiLabel";
import { formatDateTime } from "@/lib/format";

/** The model's summary, boxed and labelled. Plain text only; nothing here is a fact. */
export function AiSummaryBox({ ai }: { ai: AISummary | null | undefined }) {
  if (!ai) {
    return (
      <section className="rounded-lg border border-dashed border-slate-300 bg-white p-3 text-sm text-slate-600">
        No AI classification for this business.
      </section>
    );
  }
  return (
    <section
      className="rounded-lg border border-amber-300 bg-amber-50/60 p-3 text-sm"
      aria-label="AI summary"
      data-testid="ai-summary"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <AiLabel />
        <span className="text-xs text-slate-600">{formatDateTime(ai.created_at)}</span>
      </div>
      <p className="mt-2 whitespace-pre-wrap">
        {ai.business_summary ?? "No summary (expired or not produced)."}
      </p>
      <dl className="mt-2 grid grid-cols-[9rem_1fr] gap-y-0.5 text-xs text-slate-700">
        <dt>Industry (AI)</dt>
        <dd>
          {ai.industry ?? "unknown"}
          {ai.industry_matches_listing === false ? " · does not match the listing" : ""}
        </dd>
        <dt>Buying intent</dt>
        <dd>{ai.buying_intent ?? "unknown"}</dd>
        {ai.unknowns.length ? (
          <>
            <dt>Unknowns</dt>
            <dd>{ai.unknowns.join("; ")}</dd>
          </>
        ) : null}
      </dl>
      <details className="mt-2 text-xs text-slate-600" data-testid="ai-details">
        <summary className="cursor-pointer select-none text-slate-700">Details</summary>
        <dl className="mt-1 grid grid-cols-[7rem_1fr] gap-y-0.5">
          <dt>Model</dt>
          <dd className="font-mono">{ai.model}</dd>
          <dt>Prompt</dt>
          <dd className="font-mono">{ai.prompt_version}</dd>
          <dt>Status</dt>
          <dd>
            {ai.status}
            {ai.escalated ? " · escalated" : ""}
          </dd>
        </dl>
      </details>
    </section>
  );
}
