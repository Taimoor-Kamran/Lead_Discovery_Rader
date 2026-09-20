"""Computing the health report and keeping the condition alerts in step with it.

Every number here is read straight from the tables the pipeline already writes
(`job_runs`, `api_calls`, `ai_classifications`, `discovered_records`, `businesses`,
`match_candidates`, `crm_leads`, `website_audits`), from the RQ queue, and from the backup
directory. Nothing is sampled or estimated; an empty window reads as `None`, not `0%`.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.core import backup as backups
from app.core.config import Settings, get_settings
from app.core.health import _db_ok
from app.core.logging import get_logger
from app.core.redis import get_queue, get_redis, redis_ok
from app.modules.ai.budget import AIBudget
from app.modules.ai.models import AIClassification, ClassificationStatus
from app.modules.alerts import service as alerts
from app.modules.alerts.schemas import AlertRead
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.auth.models import User
from app.modules.businesses.models import Business
from app.modules.crm.models import CrmLead, CrmLeadStatus
from app.modules.discovery.models import ApiCall, DiscoveredRecord
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import BACKUP_VERIFY_JOB_KIND, RESOLUTION_JOB_KIND
from app.modules.monitoring.schemas import (
    AIStatus,
    AuditOutcomes,
    BackupStatus,
    CrmCounts,
    DataQuality,
    DuplicateRate,
    HealthReport,
    JobSuccess,
    KindRate,
    KindTiming,
    QueueStatus,
    ScheduledJobStatus,
    SourceErrorRate,
    SourceFreshness,
    Thresholds,
)
from app.modules.resolution.models import MatchCandidate, MatchCandidateStatus, ResolutionStatus
from app.modules.sources.models import Source
from app.workers.scheduler import LAST_RUN_KEY, LOCK_KEY, SCHEDULED_KIND_PREFIX, schedule

logger = get_logger("app.monitoring")

DAY = timedelta(hours=24)
WEEK = timedelta(days=7)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


# --- jobs -------------------------------------------------------------------------------


def job_rates(session: Session, *, since: datetime) -> list[KindRate]:
    rows = session.execute(
        select(
            JobRun.kind,
            func.count().filter(JobRun.status == JobRunStatus.done),
            func.count().filter(JobRun.status == JobRunStatus.failed),
            func.count().filter(JobRun.status == JobRunStatus.cancelled),
        )
        .where(JobRun.finished_at.is_not(None), JobRun.finished_at >= since)
        .group_by(JobRun.kind)
        .order_by(JobRun.kind)
    ).all()
    result: list[KindRate] = []
    for kind, done, failed, cancelled in rows:
        done_n, failed_n, cancelled_n = int(done), int(failed), int(cancelled)
        result.append(
            KindRate(
                kind=str(kind),
                done=done_n,
                failed=failed_n,
                cancelled=cancelled_n,
                total=done_n + failed_n + cancelled_n,
                success_rate=_rate(done_n, done_n + failed_n),
            )
        )
    return result


def timings(session: Session, *, since: datetime) -> list[KindTiming]:
    seconds = func.extract("epoch", JobRun.finished_at - JobRun.started_at)
    rows = session.execute(
        select(
            JobRun.kind,
            func.count(),
            func.percentile_cont(0.5).within_group(seconds),
            func.percentile_cont(0.95).within_group(seconds),
        )
        .where(
            JobRun.status == JobRunStatus.done,
            JobRun.started_at.is_not(None),
            JobRun.finished_at.is_not(None),
            JobRun.finished_at >= since,
        )
        .group_by(JobRun.kind)
        .order_by(JobRun.kind)
    ).all()
    return [
        KindTiming(
            kind=str(kind),
            runs=int(runs),
            median_seconds=round(float(median), 3) if median is not None else None,
            p95_seconds=round(float(p95), 3) if p95 is not None else None,
        )
        for kind, runs, median, p95 in rows
    ]


# --- external APIs ------------------------------------------------------------------------


def source_error_rates(session: Session, *, since: datetime) -> list[SourceErrorRate]:
    is_error = case(
        (ApiCall.error_class.is_not(None), 1),
        (ApiCall.status_code >= 400, 1),
        else_=0,
    )
    rows = session.execute(
        select(Source.name, func.count(ApiCall.id), func.coalesce(func.sum(is_error), 0))
        .join(ApiCall, ApiCall.source_id == Source.id)
        .where(ApiCall.created_at >= since)
        .group_by(Source.name)
        .order_by(Source.name)
    ).all()
    return [
        SourceErrorRate(
            source=str(name),
            calls=int(calls),
            errors=int(errors),
            error_rate=_rate(int(errors), int(calls)),
        )
        for name, calls, errors in rows
    ]


# --- queue and scheduler ----------------------------------------------------------------------


def queue_status(session: Session, settings: Settings) -> QueueStatus:
    redis = get_redis()
    try:
        length = int(get_queue().count)
    except Exception:
        length = 0
    raw_last: dict[Any, Any]
    try:
        lock_held = bool(redis.exists(LOCK_KEY))
        raw_last = cast(dict[Any, Any], redis.hgetall(LAST_RUN_KEY))
    except Exception:
        lock_held, raw_last = False, {}
    last_fired: dict[str, datetime | None] = {}
    for key, value in raw_last.items():
        name = key.decode() if isinstance(key, bytes) else str(key)
        text = value.decode() if isinstance(value, bytes) else str(value)
        try:
            last_fired[name] = datetime.fromisoformat(text)
        except ValueError:
            last_fired[name] = None

    statuses: list[ScheduledJobStatus] = []
    for job in schedule(settings):
        latest = session.scalars(
            select(JobRun)
            .where(JobRun.kind == job.kind)
            .order_by(JobRun.created_at.desc(), JobRun.id.desc())
            .limit(1)
        ).first()
        statuses.append(
            ScheduledJobStatus(
                name=job.name,
                cron=job.cron,
                description=job.description,
                last_fired_at=last_fired.get(job.name),
                last_run_status=latest.status.value if latest else None,
                last_run_finished_at=latest.finished_at if latest else None,
            )
        )
    return QueueStatus(
        name=settings.job_queue_name,
        length=length,
        scheduler_lock_held=lock_held,
        schedule=statuses,
    )


# --- AI --------------------------------------------------------------------------------------


def ai_status(session: Session, settings: Settings, *, day_start: datetime) -> AIStatus:
    budget = AIBudget(get_redis(), settings=settings)
    status = budget.status()
    total, reused = session.execute(
        select(
            func.count(),
            func.count().filter(AIClassification.status == ClassificationStatus.reused),
        ).where(AIClassification.created_at >= day_start)
    ).one()
    prices = budget.prices_configured
    if prices and status.budget_usd > 0:
        ratio: float | None = round(float(status.spent_usd / status.budget_usd), 4)
    elif status.call_cap > 0:
        ratio = round(status.calls / status.call_cap, 4)
    else:
        ratio = None
    return AIStatus(
        provider=settings.resolved_ai_provider,
        calls_today=status.calls,
        call_cap=status.call_cap,
        classifications_today=int(total),
        reused_today=int(reused),
        reuse_rate=_rate(int(reused), int(total)),
        spent_today_usd=float(status.spent_usd),
        budget_usd=float(status.budget_usd),
        budget_ratio=ratio,
        prices_configured=prices,
    )


# --- data quality, duplicates, CRM, freshness, audits ----------------------------------------


def data_quality(session: Session) -> DataQuality:
    records_total, records_invalid = session.execute(
        select(
            func.count(),
            func.count().filter(DiscoveredRecord.resolution_status == ResolutionStatus.invalid),
        )
    ).one()
    businesses_total, missing_city, missing_phone, missing_website = session.execute(
        select(
            func.count(),
            func.count().filter(Business.city.is_(None)),
            func.count().filter(Business.phone_e164.is_(None)),
            func.count().filter(Business.website.is_(None)),
        )
    ).one()
    return DataQuality(
        records_total=int(records_total),
        records_invalid=int(records_invalid),
        invalid_rate=_rate(int(records_invalid), int(records_total)),
        businesses_total=int(businesses_total),
        missing_city=int(missing_city),
        missing_phone=int(missing_phone),
        missing_website=int(missing_website),
    )


def duplicate_rate(session: Session, *, since: datetime) -> DuplicateRate:
    auto_merged = 0
    for summary in session.scalars(
        select(JobRun.result_summary).where(
            JobRun.kind == RESOLUTION_JOB_KIND,
            JobRun.status == JobRunStatus.done,
            JobRun.finished_at >= since,
            JobRun.result_summary.is_not(None),
        )
    ):
        if isinstance(summary, dict):
            auto_merged += int(summary.get("linked_existing") or 0)
    sent, merged, kept, pending = session.execute(
        select(
            func.count(),
            func.count().filter(MatchCandidate.status == MatchCandidateStatus.merged),
            func.count().filter(MatchCandidate.status == MatchCandidateStatus.kept_apart),
            func.count().filter(MatchCandidate.status == MatchCandidateStatus.pending),
        ).where(MatchCandidate.created_at >= since)
    ).one()
    return DuplicateRate(
        auto_merged=auto_merged,
        sent_to_review=int(sent),
        merged_by_review=int(merged),
        kept_apart=int(kept),
        pending_review=int(pending),
    )


def crm_counts(session: Session, settings: Settings, *, day_start: datetime) -> CrmCounts:
    scheduled, held, synced_today = session.execute(
        select(
            func.count().filter(CrmLead.status == CrmLeadStatus.scheduled),
            func.count().filter(CrmLead.status == CrmLeadStatus.held),
            func.count().filter(
                and_(CrmLead.status == CrmLeadStatus.synced, CrmLead.last_synced_at >= day_start)
            ),
        ).where(CrmLead.destination == settings.crm_destination)
    ).one()
    return CrmCounts(
        destination=settings.crm_destination,
        scheduled=int(scheduled),
        held=int(held),
        synced_today=int(synced_today),
    )


def source_freshness(session: Session) -> list[SourceFreshness]:
    rows = session.execute(
        select(
            Source.name,
            Source.enabled,
            func.count(DiscoveredRecord.id),
            func.max(DiscoveredRecord.last_discovered_at),
        )
        .outerjoin(DiscoveredRecord, DiscoveredRecord.source_id == Source.id)
        .group_by(Source.id, Source.name, Source.enabled)
        .order_by(Source.name)
    ).all()
    return [
        SourceFreshness(
            source=str(name), enabled=bool(enabled), records=int(count), last_discovered_at=last
        )
        for name, enabled, count, last in rows
    ]


def audit_outcomes(session: Session, *, since: datetime) -> AuditOutcomes:
    counts = {status.value: 0 for status in AuditStatus}
    for status, count in session.execute(
        select(WebsiteAudit.status, func.count())
        .where(WebsiteAudit.created_at >= since)
        .group_by(WebsiteAudit.status)
    ).all():
        counts[status.value] = int(count)
    return AuditOutcomes(
        done=counts.get("done", 0),
        robots_blocked=counts.get("robots_blocked", 0),
        unreachable=counts.get("unreachable", 0),
        failed=counts.get("failed", 0),
        skipped=counts.get("skipped", 0),
        total=sum(counts.values()),
    )


# --- backups ---------------------------------------------------------------------------------


def backup_status(session: Session, settings: Settings) -> BackupStatus:
    files = backups.list_backups(settings)
    newest = files[0] if files else None
    verify = session.scalars(
        select(JobRun)
        .where(
            JobRun.kind.in_([BACKUP_VERIFY_JOB_KIND, f"{SCHEDULED_KIND_PREFIX}backup-verify"]),
            JobRun.finished_at.is_not(None),
        )
        .order_by(JobRun.finished_at.desc(), JobRun.id.desc())
        .limit(1)
    ).first()
    summary: dict[str, Any] = dict(verify.result_summary or {}) if verify else {}
    return BackupStatus(
        directory=str(backups.backup_dir(settings)),
        backups_kept=len(files),
        keep=settings.backup_keep,
        last_backup_file=newest.name if newest else None,
        last_backup_at=newest.created_at if newest else None,
        last_backup_size_bytes=newest.size_bytes if newest else None,
        last_verify_at=verify.finished_at if verify else None,
        last_verify_ok=(verify.status == JobRunStatus.done) if verify else None,
        last_verify_file=str(summary.get("file")) if summary.get("file") else None,
        last_verify_error=(verify.error or summary.get("error")) if verify else None,
    )


# --- the report ------------------------------------------------------------------------------


def thresholds(settings: Settings) -> Thresholds:
    return Thresholds(
        job_success_rate_min=settings.alert_job_success_rate_min,
        source_error_rate_max=settings.alert_source_error_rate_max,
        ai_budget_ratio=settings.alert_ai_budget_ratio,
        backup_max_age_hours=settings.alert_backup_max_age_hours,
        queue_length_max=settings.alert_queue_length_max,
        watchdog_stale_minutes=settings.watchdog_stale_minutes,
    )


def build_report(
    session: Session, *, now: datetime | None = None, settings: Settings | None = None
) -> HealthReport:
    """Every metric on the spec's table, computed fresh. Alerts are not touched here."""
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return HealthReport(
        generated_at=moment,
        environment=config.environment,
        db=_db_ok(),
        redis=redis_ok(),
        jobs=JobSuccess(
            last_24h=job_rates(session, since=moment - DAY),
            last_7d=job_rates(session, since=moment - WEEK),
        ),
        sources=source_error_rates(session, since=moment - DAY),
        timings=timings(session, since=moment - DAY),
        queue=queue_status(session, config),
        ai=ai_status(session, config, day_start=day_start),
        data_quality=data_quality(session),
        duplicates=duplicate_rate(session, since=moment - WEEK),
        crm=crm_counts(session, config, day_start=day_start),
        freshness=source_freshness(session),
        audits=audit_outcomes(session, since=moment - WEEK),
        backups=backup_status(session, config),
        thresholds=thresholds(config),
        alerts=alerts.list_alerts(session),
    )


def _installed_at(session: Session) -> datetime | None:
    """When this installation came to life: the oldest user. Used to not demand a backup
    of a stack that is minutes old."""
    return session.scalar(select(func.min(User.created_at)))


def evaluate_alerts(
    session: Session,
    *,
    report: HealthReport | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[AlertRead]:
    """Raise or clear every condition rule from a report. Returns the open, unacknowledged
    alerts afterwards (what the banner shows)."""
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    data = report or build_report(session, now=moment, settings=config)

    low = [
        rate
        for rate in data.jobs.last_24h
        if rate.success_rate is not None and rate.success_rate < config.alert_job_success_rate_min
    ]
    alerts.sync_condition(
        session,
        alerts.RULE_JOB_SUCCESS_RATE,
        holds=bool(low),
        message=(
            "Job success rate below "
            f"{int(config.alert_job_success_rate_min * 100)}% in the last 24 h for: "
            + ", ".join(f"{r.kind} ({int((r.success_rate or 0) * 100)}%)" for r in low)
        ),
        details={"kinds": [r.model_dump() for r in low]},
        now=moment,
    )

    noisy = [
        s
        for s in data.sources
        if s.error_rate is not None and s.error_rate > config.alert_source_error_rate_max
    ]
    alerts.sync_condition(
        session,
        alerts.RULE_SOURCE_ERROR_RATE,
        holds=bool(noisy),
        message=(
            "External API error rate above "
            f"{int(config.alert_source_error_rate_max * 100)}% in the last 24 h for: "
            + ", ".join(f"{s.source} ({int((s.error_rate or 0) * 100)}%)" for s in noisy)
        ),
        details={"sources": [s.model_dump() for s in noisy]},
        now=moment,
    )

    alerts.sync_condition(
        session,
        alerts.RULE_CRM_HELD,
        holds=data.crm.held > 0,
        message=f"{data.crm.held} CRM lead(s) are held and need a human on the CRM page",
        details={"held": data.crm.held, "destination": data.crm.destination},
        now=moment,
    )

    ratio = data.ai.budget_ratio
    alerts.sync_condition(
        session,
        alerts.RULE_AI_BUDGET,
        holds=ratio is not None and ratio >= config.alert_ai_budget_ratio,
        message=(
            f"AI spend is at {int((ratio or 0) * 100)}% of today's budget "
            f"(${data.ai.spent_today_usd:.2f} of ${data.ai.budget_usd:.2f}"
            + ("" if data.ai.prices_configured else f"; {data.ai.calls_today} calls by count")
            + ")"
        ),
        details=data.ai.model_dump(),
        now=moment,
    )

    max_age = timedelta(hours=config.alert_backup_max_age_hours)
    installed = _installed_at(session)
    last = data.backups.last_backup_at
    if last is not None:
        stale_backup = (moment - last.astimezone(UTC)) > max_age
        backup_message = (
            f"The newest backup ({data.backups.last_backup_file}) is older than "
            f"{config.alert_backup_max_age_hours} h"
        )
    else:
        stale_backup = installed is not None and (moment - installed) > max_age
        backup_message = (
            f"No backup has been made in {config.alert_backup_max_age_hours} h "
            "(none exists yet); run `make backup` and check the scheduler"
        )
    alerts.sync_condition(
        session,
        alerts.RULE_BACKUP_AGE,
        holds=stale_backup,
        message=backup_message,
        details={"last_backup_at": last.isoformat() if last else None},
        now=moment,
    )

    alerts.sync_condition(
        session,
        alerts.RULE_QUEUE_LENGTH,
        holds=data.queue.length > config.alert_queue_length_max,
        message=(
            f"The job queue holds {data.queue.length} jobs (limit {config.alert_queue_length_max});"
            " is the worker running?"
        ),
        severity="critical",
        details={"length": data.queue.length},
        now=moment,
    )
    session.flush()
    return alerts.list_alerts(session)


def health(session: Session, *, now: datetime | None = None) -> HealthReport:
    """What `GET /admin/health` returns: a fresh report with the alert rules applied."""
    moment = now or datetime.now(UTC)
    report = build_report(session, now=moment)
    report.alerts = evaluate_alerts(session, report=report, now=moment)
    return report


__all__ = ["build_report", "evaluate_alerts", "health"]
