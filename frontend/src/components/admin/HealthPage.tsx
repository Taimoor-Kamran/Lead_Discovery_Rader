"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/ErrorNote";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  SkeletonLines,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
  useToast,
} from "@/components/ui";
import { acknowledgeAlert, ApiError, getAdminHealth, type AlertRead, type HealthReport } from "@/lib/api";
import { loadFailed } from "@/lib/errors";
import { formatDateTime, percent } from "@/lib/format";

function rate(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : percent(value);
}

function seconds(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)} s`;
}

export function AlertList({
  alerts,
  onAcknowledge,
  busy,
}: {
  alerts: AlertRead[];
  onAcknowledge: (alert: AlertRead) => void;
  busy: boolean;
}) {
  if (!alerts.length)
    return (
      <p data-testid="no-alerts" className="text-base text-ok">
        No open alerts.
      </p>
    );
  return (
    <ul className="flex flex-col gap-2">
      {alerts.map((alert) => (
        <li
          key={alert.id}
          data-testid="alert"
          data-severity={alert.severity}
          className={`flex flex-wrap items-center gap-3 rounded-lg border p-3 text-base text-ink ${
            alert.severity === "critical" ? "border-risk bg-risk-tint" : "border-warn bg-warn-tint"
          }`}
        >
          <Badge tone={alert.severity === "critical" ? "risk" : "warn"}>{alert.severity}</Badge>
          <span className="flex-1">{alert.message}</span>
          <span className="text-sm text-ink-soft">since {formatDateTime(alert.first_seen_at)}</span>
          <Button size="sm" disabled={busy} onClick={() => onAcknowledge(alert)}>
            Acknowledge
          </Button>
        </li>
      ))}
    </ul>
  );
}

export function HealthPage() {
  const { show } = useToast();
  const [report, setReport] = useState<HealthReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setReport(await getAdminHealth());
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : loadFailed("the health report"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function acknowledge(alert: AlertRead) {
    setBusy(true);
    try {
      await acknowledgeAlert(alert.id);
      show({ tone: "success", message: "Alert acknowledged." });
      await load();
    } catch (caught) {
      show({ tone: "error", message: caught instanceof ApiError ? caught.message : "Could not acknowledge" });
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!report) return <SkeletonLines lines={6} />;
  const t = report.thresholds;

  return (
    <div className="flex flex-col gap-5" data-testid="health">
      <PageHeader
        title="Health"
        description={`The ${report.environment} stack, as of ${formatDateTime(report.generated_at)}.`}
        actions={
          <>
            <Badge tone={report.db ? "ok" : "risk"}>Database {report.db ? "ok" : "down"}</Badge>
            <Badge tone={report.redis ? "ok" : "risk"}>Redis {report.redis ? "ok" : "down"}</Badge>
            <Button onClick={() => void load()}>Refresh</Button>
          </>
        }
      />

      <section aria-labelledby="h-alerts">
        <h2 id="h-alerts" className="mb-2 text-md font-semibold text-ink">Alerts</h2>
        <AlertList alerts={report.alerts} onAcknowledge={(alert) => void acknowledge(alert)} busy={busy} />
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
        <Metric title="Job success rate by kind" testId="m-jobs">
          <Table minWidth="0" className="text-sm">
            <THead>
              <tr>
                <Th>Kind</Th>
                <Th numeric>24 h</Th>
                <Th numeric>7 d</Th>
              </tr>
            </THead>
            <TBody>
              {Array.from(new Set([...report.jobs.last_24h, ...report.jobs.last_7d].map((r) => r.kind)))
                .sort()
                .map((kind) => {
                  const day = report.jobs.last_24h.find((r) => r.kind === kind);
                  const week = report.jobs.last_7d.find((r) => r.kind === kind);
                  return (
                    <Tr key={kind}>
                      <Td>{kind}</Td>
                      <Td
                        numeric
                        className={
                          day && day.success_rate !== null && day.success_rate < t.job_success_rate_min
                            ? "text-risk"
                            : ""
                        }
                      >
                        {day ? `${rate(day.success_rate)} (${day.done}/${day.total})` : "—"}
                      </Td>
                      <Td numeric>{week ? `${rate(week.success_rate)} (${week.done}/${week.total})` : "—"}</Td>
                    </Tr>
                  );
                })}
            </TBody>
          </Table>
          {!report.jobs.last_7d.length ? (
            <EmptyState
              title="No runs in the last 7 days."
              description="Run a search to give the pipeline something to do."
            />
          ) : null}
        </Metric>

        <Metric title="External API error rate (24 h)" testId="m-sources">
          {report.sources.length ? (
            <ul className="flex flex-col gap-1 text-sm">
              {report.sources.map((s) => (
                <li key={s.source} className={s.error_rate !== null && s.error_rate > t.source_error_rate_max ? "text-risk" : ""}>
                  {s.source}: {rate(s.error_rate)} ({s.errors} of {s.calls} calls)
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-ink-soft">No API calls in the last 24 h.</p>}
        </Metric>

        <Metric title="Processing time per job kind (24 h)" testId="m-timings">
          {report.timings.length ? (
            <ul className="flex flex-col gap-1 text-sm">
              {report.timings.map((row) => (
                <li key={row.kind}>
                  {row.kind}: median {seconds(row.median_seconds)}, p95 {seconds(row.p95_seconds)}, {row.runs} run(s)
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-ink-soft">Nothing finished in the last 24 h.</p>}
        </Metric>

        <Metric title="Queue and scheduler" testId="m-queue">
          <p className="text-sm">
            Queue <strong>{report.queue.name}</strong>: {report.queue.length} job(s) waiting (alert above{" "}
            {t.queue_length_max}). The scheduler lock is{" "}
            {report.queue.scheduler_lock_held ? "held" : "free"}.
          </p>
          <ul className="mt-1 flex flex-col gap-1 text-sm">
            {report.queue.schedule.map((job) => (
              <li key={job.name}>
                <code className="font-mono">{job.name}</code> ({job.cron}): last fired{" "}
                {job.last_fired_at ? formatDateTime(job.last_fired_at) : "never"}, last run{" "}
                {job.last_run_status ?? "none"}
              </li>
            ))}
          </ul>
        </Metric>

        <Metric title="AI today" testId="m-ai">
          {/* Rules-only is a supported configuration, so "off" is the whole report: quoting a
              0-of-500 call count against a budget nobody is spending reads as a fault. */}
          {report.ai.provider === "disabled" ? (
            <p className="text-sm" data-testid="ai-off">
              AI is off. Opportunities come from the deterministic rules alone, and no model is
              called, so there is no budget to report.
            </p>
          ) : (
            <p className="text-sm">
              {report.ai.provider}: {report.ai.calls_today} of {report.ai.call_cap} calls, reuse
              rate {rate(report.ai.reuse_rate)} ({report.ai.reused_today}/
              {report.ai.classifications_today}). Spent ${report.ai.spent_today_usd.toFixed(2)} of $
              {report.ai.budget_usd.toFixed(2)}{" "}
              {report.ai.prices_configured ? "" : "(prices not configured: counted by calls)"},
              which is {rate(report.ai.budget_ratio)} of the budget (alert at{" "}
              {percent(t.ai_budget_ratio)})
            </p>
          )}
        </Metric>

        <Metric title="Data quality" testId="m-quality">
          <p className="text-sm">
            Invalid records {rate(report.data_quality.invalid_rate)} (
            {report.data_quality.records_invalid}/{report.data_quality.records_total}). Of{" "}
            {report.data_quality.businesses_total} businesses, {report.data_quality.missing_city} have no
            city, {report.data_quality.missing_phone} no phone and {report.data_quality.missing_website} no
            website
          </p>
        </Metric>

        <Metric title="Duplicates (7 d)" testId="m-duplicates">
          <p className="text-sm">
            Auto-merged {report.duplicates.auto_merged}, sent to review{" "}
            {report.duplicates.sent_to_review} (merged {report.duplicates.merged_by_review}, kept apart{" "}
            {report.duplicates.kept_apart}, pending {report.duplicates.pending_review})
          </p>
        </Metric>

        <Metric title="CRM" testId="m-crm">
          <p className="text-sm">
            {report.crm.destination}: scheduled {report.crm.scheduled}, held {report.crm.held}, synced
            today {report.crm.synced_today}
          </p>
        </Metric>

        <Metric title="Source freshness" testId="m-freshness">
          {report.freshness.length ? (
            <ul className="flex flex-col gap-1 text-sm">
              {report.freshness.map((s) => (
                <li key={s.source}>
                  {s.source}
                  {s.enabled ? "" : " (disabled)"}: {s.records} record(s), last seen{" "}
                  {s.last_discovered_at ? formatDateTime(s.last_discovered_at) : "never"}
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-ink-soft">No sources registered.</p>}
        </Metric>

        <Metric title="Audit outcomes (7 d)" testId="m-audits">
          <p className="text-sm">
            Done {report.audits.done}, blocked by robots {report.audits.robots_blocked}, unreachable{" "}
            {report.audits.unreachable}, failed {report.audits.failed}, skipped {report.audits.skipped}, of{" "}
            {report.audits.total} in total
          </p>
        </Metric>

        <Metric title="Backups" testId="m-backups">
          <p className="text-sm">
            {report.backups.backups_kept} of {report.backups.keep} kept in{" "}
            <code className="font-mono">{report.backups.directory}</code>. Last backup{" "}
            {report.backups.last_backup_at
              ? `${formatDateTime(report.backups.last_backup_at)} (${report.backups.last_backup_file})`
              : "none yet"}{" "}
            (alert after {t.backup_max_age_hours} h)
          </p>
          <p className="text-sm">
            Last verify{" "}
            {report.backups.last_verify_at
              ? `${formatDateTime(report.backups.last_verify_at)}: ${report.backups.last_verify_ok ? "OK" : `failed — ${report.backups.last_verify_error ?? "unknown"}`}`
              : "never"}
          </p>
        </Metric>
      </div>
    </div>
  );
}

/** One metric block. The page is a grid of these; each one is a Card with a heading. */
function Metric({ title, testId, children }: { title: string; testId: string; children: React.ReactNode }) {
  return (
    <Card data-testid={testId}>
      <h2 className="mb-2 text-base font-semibold text-ink">{title}</h2>
      {children}
    </Card>
  );
}
