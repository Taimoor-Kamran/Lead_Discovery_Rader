"use client";

import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/Toast";
import { acknowledgeAlert, ApiError, getAdminHealth, type AlertRead, type HealthReport } from "@/lib/api";
import { formatDateTime, percent } from "@/lib/format";

function rate(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : percent(value);
}

function seconds(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)} s`;
}

export function AlertList({ alerts, onAcknowledge, busy }: { alerts: AlertRead[]; onAcknowledge: (alert: AlertRead) => void; busy: boolean }) {
  if (!alerts.length) return <p data-testid="no-alerts" className="text-sm text-teal-700">No open alerts.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {alerts.map((alert) => (
        <li key={alert.id} data-testid="alert" data-severity={alert.severity} className={`flex flex-wrap items-center gap-3 rounded border p-3 text-sm ${alert.severity === "critical" ? "border-red-300 bg-red-50 text-red-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}>
          <span className="chip border-current uppercase">{alert.severity}</span>
          <span className="flex-1">{alert.message}</span>
          <span className="text-xs">since {formatDateTime(alert.first_seen_at)}</span>
          <button type="button" className="btn-secondary !py-0.5" disabled={busy} onClick={() => onAcknowledge(alert)}>Acknowledge</button>
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
      setError(caught instanceof ApiError ? caught.message : "Could not load the health report");
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

  if (error) return <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>;
  if (!report) return <p className="text-sm text-slate-600">Loading…</p>;
  const t = report.thresholds;

  return (
    <div className="flex flex-col gap-5" data-testid="health">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-navy">Health</h1>
          <p className="text-sm text-slate-600">
            {report.environment} · generated {formatDateTime(report.generated_at)} · database {report.db ? "ok" : "DOWN"} · redis {report.redis ? "ok" : "DOWN"}
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={() => void load()}>Refresh</button>
      </header>

      <section aria-labelledby="h-alerts">
        <h2 id="h-alerts" className="mb-2 font-semibold">Alerts</h2>
        <AlertList alerts={report.alerts} onAcknowledge={(alert) => void acknowledge(alert)} busy={busy} />
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Job success rate by kind" testId="m-jobs">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-600"><th>Kind</th><th>24 h</th><th>7 d</th></tr></thead>
            <tbody>
              {Array.from(new Set([...report.jobs.last_24h, ...report.jobs.last_7d].map((r) => r.kind))).sort().map((kind) => {
                const day = report.jobs.last_24h.find((r) => r.kind === kind);
                const week = report.jobs.last_7d.find((r) => r.kind === kind);
                return (
                  <tr key={kind}>
                    <td>{kind}</td>
                    <td className={day && day.success_rate !== null && day.success_rate < t.job_success_rate_min ? "text-red-800" : ""}>{day ? `${rate(day.success_rate)} (${day.done}/${day.total})` : "—"}</td>
                    <td>{week ? `${rate(week.success_rate)} (${week.done}/${week.total})` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!report.jobs.last_7d.length ? <p className="text-xs text-slate-600">No runs in the last 7 days.</p> : null}
        </Card>

        <Card title="External API error rate (24 h)" testId="m-sources">
          {report.sources.length ? (
            <ul className="text-xs">
              {report.sources.map((s) => (
                <li key={s.source} className={s.error_rate !== null && s.error_rate > t.source_error_rate_max ? "text-red-800" : ""}>
                  {s.source}: {rate(s.error_rate)} ({s.errors} of {s.calls} calls)
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-slate-600">No API calls in the last 24 h.</p>}
        </Card>

        <Card title="Processing time per job kind (24 h)" testId="m-timings">
          {report.timings.length ? (
            <ul className="text-xs">
              {report.timings.map((row) => (
                <li key={row.kind}>{row.kind}: median {seconds(row.median_seconds)} · p95 {seconds(row.p95_seconds)} · {row.runs} run(s)</li>
              ))}
            </ul>
          ) : <p className="text-xs text-slate-600">Nothing finished in the last 24 h.</p>}
        </Card>

        <Card title="Queue and scheduler" testId="m-queue">
          <p className="text-xs">Queue <strong>{report.queue.name}</strong>: {report.queue.length} job(s) waiting (alert above {t.queue_length_max}) · scheduler lock {report.queue.scheduler_lock_held ? "held" : "free"}</p>
          <ul className="mt-1 text-xs">
            {report.queue.schedule.map((job) => (
              <li key={job.name}>
                <code>{job.name}</code> ({job.cron}): last fired {job.last_fired_at ? formatDateTime(job.last_fired_at) : "never"} · last run {job.last_run_status ?? "none"}
              </li>
            ))}
          </ul>
        </Card>

        <Card title="AI today" testId="m-ai">
          <p className="text-xs">
            {report.ai.provider} · {report.ai.calls_today} of {report.ai.call_cap} calls · reuse rate {rate(report.ai.reuse_rate)} ({report.ai.reused_today}/{report.ai.classifications_today}) ·
            ${report.ai.spent_today_usd.toFixed(2)} of ${report.ai.budget_usd.toFixed(2)} {report.ai.prices_configured ? "" : "(prices not configured: counted by calls)"} · {rate(report.ai.budget_ratio)} of budget (alert at {percent(t.ai_budget_ratio)})
          </p>
        </Card>

        <Card title="Data quality" testId="m-quality">
          <p className="text-xs">
            Invalid records {rate(report.data_quality.invalid_rate)} ({report.data_quality.records_invalid}/{report.data_quality.records_total}) · {report.data_quality.businesses_total} businesses: {report.data_quality.missing_city} without city, {report.data_quality.missing_phone} without phone, {report.data_quality.missing_website} without website
          </p>
        </Card>

        <Card title="Duplicates (7 d)" testId="m-duplicates">
          <p className="text-xs">
            auto-merged {report.duplicates.auto_merged} · sent to review {report.duplicates.sent_to_review} (merged {report.duplicates.merged_by_review}, kept apart {report.duplicates.kept_apart}, pending {report.duplicates.pending_review})
          </p>
        </Card>

        <Card title="CRM" testId="m-crm">
          <p className="text-xs">{report.crm.destination}: scheduled {report.crm.scheduled} · held {report.crm.held} · synced today {report.crm.synced_today}</p>
        </Card>

        <Card title="Source freshness" testId="m-freshness">
          {report.freshness.length ? (
            <ul className="text-xs">
              {report.freshness.map((s) => (
                <li key={s.source}>{s.source}{s.enabled ? "" : " (disabled)"}: {s.records} record(s), last seen {s.last_discovered_at ? formatDateTime(s.last_discovered_at) : "never"}</li>
              ))}
            </ul>
          ) : <p className="text-xs text-slate-600">No sources registered.</p>}
        </Card>

        <Card title="Audit outcomes (7 d)" testId="m-audits">
          <p className="text-xs">done {report.audits.done} · robots {report.audits.robots_blocked} · unreachable {report.audits.unreachable} · failed {report.audits.failed} · skipped {report.audits.skipped} · total {report.audits.total}</p>
        </Card>

        <Card title="Backups" testId="m-backups">
          <p className="text-xs">
            {report.backups.backups_kept} of {report.backups.keep} kept in <code>{report.backups.directory}</code> · last backup {report.backups.last_backup_at ? `${formatDateTime(report.backups.last_backup_at)} (${report.backups.last_backup_file})` : "none yet"} (alert after {t.backup_max_age_hours} h)
          </p>
          <p className="text-xs">
            last verify {report.backups.last_verify_at ? `${formatDateTime(report.backups.last_verify_at)}: ${report.backups.last_verify_ok ? "OK" : `FAILED — ${report.backups.last_verify_error ?? "unknown"}`}` : "never"}
          </p>
        </Card>
      </div>
    </div>
  );
}

function Card({ title, testId, children }: { title: string; testId: string; children: React.ReactNode }) {
  return (
    <section data-testid={testId} className="rounded-lg border border-slate-200 bg-white p-3">
      <h2 className="mb-2 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}
