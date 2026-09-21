"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import { Badge, type BadgeTone, Button, buttonClass, Card, PageHeader, useToast } from "@/components/ui";
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
import { loadFailed } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";
import { canRunSearches } from "@/lib/roles";

const STAGE_LABELS: Record<string, string> = {
  discovery: "Discovery",
  resolution: "Resolution",
  audit: "Audit",
  classification: "Classification",
};
const POLL_MS = 5000;

/** A stage carries its status as a word; the tone only repeats what the word already says. */
function stageTone(stage: PipelineStage): BadgeTone {
  const status = stage.run?.status;
  if (!status) return "neutral";
  if (status === "done") return "ok";
  if (status === "failed") return "risk";
  if (status === "cancelled") return "neutral";
  return "warn";
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
      setError(caught instanceof ApiError ? caught.message : loadFailed("this search"));
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
    <div className="flex flex-col gap-5" data-testid="pipeline">
      <PageHeader
        meta={
          <Link href="/searches" className="rounded font-medium text-accent underline underline-offset-2">
            ← Searches
          </Link>
        }
        title={job?.name ?? "Search"}
        description={
          job
            ? `${job.industry} in ${describeGeo(job.geo)}, at most ${job.max_results ?? "the default number of"} results. Created ${formatDateTime(job.created_at)}.`
            : undefined
        }
        actions={
          <>
            <Link
              href={reviewHref}
              className={buttonClass()}
              data-testid="review-link"
            >
              Open the review queue
            </Link>
            {mayRun ? (
              <Button
                variant="primary"
                disabled={busy || (estimate !== null && !estimate.can_run) || stillWorking(pipeline)}
                title={estimate && !estimate.can_run ? estimate.blockers[0] : undefined}
                onClick={() => void rerun()}
              >
                {pipeline?.discovery_run_id ? "Run again" : "Run"}
              </Button>
            ) : null}
          </>
        }
      />
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {mayRun ? <EstimatePanel estimate={estimate} /> : null}

      <ol className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4" aria-label="Pipeline">
        {(pipeline?.stages ?? []).map((stage) => (
          <li key={stage.stage} data-testid={`stage-${stage.stage}`}>
            <Card className="h-full">
              <header className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-md font-semibold text-ink">{STAGE_LABELS[stage.stage] ?? stage.stage}</h2>
                <Badge tone={stageTone(stage)} data-testid="stage-status">
                  {stage.run ? stage.run.status : "not started"}
                </Badge>
              </header>
              {stage.run ? (
                <p className="mt-2 text-sm text-ink-soft">
                  <span className="font-mono tabular-nums">
                    {stage.run.progress_done}/{stage.run.progress_total}
                  </span>
                  <span className="ml-2">
                    {stage.run.finished_at
                      ? `finished ${formatDateTime(stage.run.finished_at)}`
                      : stage.run.started_at
                        ? `started ${formatDateTime(stage.run.started_at)}`
                        : `queued ${formatDateTime(stage.run.created_at)}`}
                  </span>
                </p>
              ) : null}
              {Object.keys(stage.counts).length ? (
                <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-sm">
                  {Object.entries(stage.counts).map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="text-ink-soft">{key.replace(/_/g, " ")}</dt>
                      <dd className="font-mono tabular-nums" data-testid={`count-${stage.stage}-${key}`}>
                        {String(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {stage.error ? (
                <p role="alert" className="mt-3 text-sm text-risk">
                  {stage.error}
                </p>
              ) : null}
            </Card>
          </li>
        ))}
      </ol>
      {pipeline && !pipeline.discovery_run_id ? (
        <p className="text-base text-ink-soft">This search has not run yet. Run it to fill the queue.</p>
      ) : null}
    </div>
  );
}
