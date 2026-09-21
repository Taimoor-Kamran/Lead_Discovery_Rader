"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/ui";
import { EstimatePanel } from "@/components/searches/EstimatePanel";
import { confirmRerun, ranRecently } from "@/components/searches/rerun";
import { describeGeo } from "@/components/searches/Searches";
import {
  ApiError,
  getSearchJob,
  getSearchJobEstimate,
  getSearchPipeline,
  runSearchJob,
  type CostEstimate,
  type PipelineRead,
  type PipelineStage,
  type SearchJobRead,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { canRunSearches } from "@/lib/roles";

const STAGE_LABELS: Record<string, string> = {
  discovery: "Discovery",
  resolution: "Resolution",
  audit: "Audit",
  classification: "Classification",
};
const POLL_MS = 5000;

function stageTone(stage: PipelineStage): string {
  const status = stage.run?.status;
  if (!status) return "border-slate-200 bg-slate-50 text-slate-600";
  if (status === "done") return "border-teal-300 bg-teal-50";
  if (status === "failed") return "border-red-300 bg-red-50";
  if (status === "cancelled") return "border-slate-300 bg-slate-100";
  return "border-amber-300 bg-amber-50";
}

export function stillWorking(pipeline: PipelineRead | null): boolean {
  if (!pipeline) return false;
  return pipeline.stages.some((stage) => stage.run && (stage.run.status === "queued" || stage.run.status === "running"));
}

export function SearchPipeline({ searchJobId }: { searchJobId: string }) {
  const { show } = useToast();
  const { user } = useAuth();
  const mayRun = user ? canRunSearches(user.role) : false;
  const [job, setJob] = useState<SearchJobRead | null>(null);
  const [pipeline, setPipeline] = useState<PipelineRead | null>(null);
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [detail, flow] = await Promise.all([getSearchJob(searchJobId), getSearchPipeline(searchJobId)]);
      setJob(detail);
      setPipeline(flow);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the search");
    }
  }, [searchJobId]);

  useEffect(() => {
    void load();
    if (mayRun) getSearchJobEstimate(searchJobId).then(setEstimate).catch(() => setEstimate(null));
  }, [load, mayRun, searchJobId]);

  useEffect(() => {
    if (!stillWorking(pipeline)) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [pipeline, load]);

  async function rerun() {
    const last = pipeline?.stages[0]?.run ?? null;
    if (ranRecently(last) && !confirmRerun()) return;
    setBusy(true);
    try {
      await runSearchJob(searchJobId);
      show({ tone: "success", message: "The search is running." });
      await load();
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Could not run the search" });
    } finally {
      setBusy(false);
    }
  }

  const query = pipeline?.review_queue_query ?? {};
  const reviewHref = `/review${Object.keys(query).length ? `?${new URLSearchParams(query).toString()}` : ""}`;

  return (
    <div className="flex flex-col gap-4" data-testid="pipeline">
      <p><Link href="/searches" className="text-sm text-teal-700 underline">← Searches</Link></p>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-navy">{job?.name ?? "Search"}</h1>
          {job ? (
            <p className="text-sm text-slate-600">
              {job.industry} · {describeGeo(job.geo)} · max {job.max_results ?? "default"} results · created {formatDateTime(job.created_at)}
            </p>
          ) : null}
        </div>
        <div className="flex gap-2">
          <Link href={reviewHref} className="btn-secondary" data-testid="review-link">Open the review queue</Link>
          {mayRun ? (
            <button type="button" className="btn-primary" disabled={busy || (estimate !== null && !estimate.can_run) || stillWorking(pipeline)} title={estimate && !estimate.can_run ? estimate.blockers[0] : undefined} onClick={() => void rerun()}>
              {pipeline?.discovery_run_id ? "Run again" : "Run"}
            </button>
          ) : null}
        </div>
      </header>
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      {mayRun ? <EstimatePanel estimate={estimate} /> : null}

      <ol className="grid grid-cols-1 gap-3 md:grid-cols-4" aria-label="Pipeline">
        {(pipeline?.stages ?? []).map((stage) => (
          <li key={stage.stage} data-testid={`stage-${stage.stage}`} className={`rounded-lg border p-3 text-sm ${stageTone(stage)}`}>
            <h2 className="font-semibold">{STAGE_LABELS[stage.stage] ?? stage.stage}</h2>
            <p data-testid="stage-status" className="text-xs uppercase tracking-wide">{stage.run ? stage.run.status : "not started"}</p>
            {stage.run ? (
              <p className="text-xs text-slate-600">
                {stage.run.progress_done}/{stage.run.progress_total} · {stage.run.finished_at ? `finished ${formatDateTime(stage.run.finished_at)}` : stage.run.started_at ? `started ${formatDateTime(stage.run.started_at)}` : `queued ${formatDateTime(stage.run.created_at)}`}
              </p>
            ) : null}
            {Object.keys(stage.counts).length ? (
              <dl className="mt-2 grid grid-cols-2 gap-x-2 text-xs">
                {Object.entries(stage.counts).map(([key, value]) => (
                  <div key={key} className="contents">
                    <dt className="text-slate-600">{key.replace(/_/g, " ")}</dt>
                    <dd data-testid={`count-${stage.stage}-${key}`}>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            {stage.error ? <p role="alert" className="mt-2 text-xs text-red-800">{stage.error}</p> : null}
          </li>
        ))}
      </ol>
      {pipeline && !pipeline.discovery_run_id ? <p className="text-sm text-slate-600">This search has not run yet.</p> : null}
    </div>
  );
}
