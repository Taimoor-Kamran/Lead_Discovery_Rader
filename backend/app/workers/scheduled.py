"""Handlers for the `scheduled:<name>` job kinds the scheduler queues.

Each one is an ordinary job handler: it runs inside the RQ worker through the same
state machine, retry policy and audit trail as a discovery run, and writes what it did
to `result_summary` so the health page can show it. A backup that fails to verify is
not retried (there is nothing transient about a bad dump); it raises an alert instead.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.state import transition
from app.workers.scheduler import (
    JOB_BACKUP,
    JOB_BACKUP_VERIFY,
    JOB_CRM_SYNC,
    JOB_PURGE_EXPIRED,
    JOB_WATCHDOG,
    SCHEDULED_KIND_PREFIX,
)

logger = get_logger("app.scheduled")

WORKER_LOST = (
    "worker lost: the run was still `running` with no progress for longer than the stale-run limit"
)
QUEUE_LOST = (
    "queue lost: the run was still `queued` after the stale-run limit, with no job on the queue"
)


class ScheduledJobError(Exception):
    """A scheduled job that failed for a reason a retry would not fix."""

    retryable = False


def run_crm_sync(session: Session, run: JobRun) -> None:
    from app.modules.crm import service as crm

    processed = crm.sync_due(session)
    run.result_summary = {"processed": processed}


def run_purge_expired(session: Session, run: JobRun) -> None:
    from app.modules.discovery.service import purge_expired

    purged = purge_expired(session)
    run.result_summary = {
        "records": purged.records,
        "field_values": purged.field_values,
        "businesses_recomputed": purged.businesses_recomputed,
        "audit_page_texts": purged.audit_page_texts,
        "ai_classifications": purged.ai_classifications,
    }


def run_backup(session: Session, run: JobRun) -> None:
    from app.core.backup import create_backup

    run.result_summary = create_backup().summary()


def run_backup_verify(session: Session, run: JobRun) -> None:
    from app.core.backup import verify_backup
    from app.modules.alerts import service as alerts

    result = verify_backup()
    run.result_summary = result.summary()
    if not result.ok:
        alerts.raise_alert(
            session,
            alerts.RULE_BACKUP_VERIFY_FAILED,
            f"Backup verify failed: {result.error}",
            severity="critical",
            details=result.summary(),
        )
        raise ScheduledJobError(result.error or "backup verify failed")


def stale_runs(session: Session, *, now: datetime, exclude: JobRun | None = None) -> list[JobRun]:
    """Runs `running` with no progress for the configured limit — a worker died under them.

    Measured from `updated_at`, which every checkpoint moves, not from `started_at`: a
    long run that is still making progress is alive. Until v0.11.0 it was `started_at`,
    and an audit of 54 businesses — about 30 minutes, one at a time — could not finish
    inside the 30-minute limit however healthy it was.
    """
    limit = now - timedelta(minutes=get_settings().watchdog_stale_minutes)
    stmt = select(JobRun).where(
        JobRun.status == JobRunStatus.running,
        JobRun.started_at.is_not(None),
        JobRun.updated_at < limit,
    )
    if exclude is not None:
        stmt = stmt.where(JobRun.id != exclude.id)
    return list(session.scalars(stmt.order_by(JobRun.started_at.asc())))


def abandoned_runs(
    session: Session, *, now: datetime, exclude: JobRun | None = None
) -> list[JobRun]:
    """Runs still `queued` past the limit that no longer have a job on the RQ queue.

    The other half of "stuck". A run whose worker died *before* it reached `running` —
    or whose attempt was rolled back out of `running` — is `queued` with nothing left to
    pick it up. It is not `running`, so `stale_runs` never sees it, and it is not
    terminal, so the scheduler keeps skipping its job: exactly how one
    `scheduled:crm-sync` run held every later CRM sync for a day (spec v0.9.0).

    Redis is the arbiter, not the clock: a run that really is waiting its turn on a busy
    queue still has its job and is left alone. If Redis cannot be reached the sweep is
    skipped entirely — failing runs on a guess is worse than failing them late.
    """
    limit = now - timedelta(minutes=get_settings().watchdog_stale_minutes)
    stmt = select(JobRun).where(JobRun.status == JobRunStatus.queued, JobRun.created_at < limit)
    if exclude is not None:
        stmt = stmt.where(JobRun.id != exclude.id)
    candidates = list(session.scalars(stmt.order_by(JobRun.created_at.asc())))
    if not candidates:
        return []
    try:
        known = _queued_job_ids()
    except Exception:
        logger.warning("could not read the queue; leaving queued runs alone", exc_info=True)
        return []
    return [run for run in candidates if str(run.id) not in known]


def _queued_job_ids() -> set[str]:
    """Every job id RQ still has work for. `enqueue_run` uses the run id as the job id.

    The waiting queue plus the started registry. Deliberately *not* the failed registry:
    a job whose RQ side has already failed is precisely one nothing will run again.
    """
    from rq.registry import StartedJobRegistry

    from app.core.redis import get_queue

    queue = get_queue()
    known = set(queue.get_job_ids())
    known.update(StartedJobRegistry(queue=queue).get_job_ids())
    return known


def run_watchdog(session: Session, run: JobRun, *, now: datetime | None = None) -> None:
    """Fail abandoned runs with their reason, alert, then re-evaluate every alert rule."""
    from app.modules.alerts import service as alerts

    moment = now or datetime.now(UTC)
    failed: list[dict[str, Any]] = []
    stuck: list[tuple[JobRun, str]] = [
        *((item, WORKER_LOST) for item in stale_runs(session, now=moment, exclude=run)),
        *((item, QUEUE_LOST) for item in abandoned_runs(session, now=moment, exclude=run)),
    ]
    for item, reason in stuck:
        transition(session, item, JobRunStatus.failed, error=reason)
        failed.append({"job_run_id": str(item.id), "kind": item.kind})
        alerts.raise_alert(
            session,
            alerts.RULE_STALE_JOB,
            f"A {item.kind} run was stuck in `{_was(reason)}` with no progress for more than "
            f"{get_settings().watchdog_stale_minutes} minutes and was failed "
            f"({reason.split(':')[0]})",
            details={"job_run_id": str(item.id), "kind": item.kind, "reason": reason},
            now=moment,
        )
        logger.warning(
            "stale run failed by the watchdog",
            extra={"job_run_id": str(item.id), "kind": item.kind, "reason": reason},
        )

    from app.modules.monitoring import service as monitoring

    evaluated = monitoring.evaluate_alerts(session, now=moment)
    run.result_summary = {
        "stale_runs_failed": len(failed),
        "stale": failed,
        "alerts_open": len(evaluated),
    }


def _was(reason: str) -> str:
    return "running" if reason == WORKER_LOST else "queued"


HANDLERS = {
    f"{SCHEDULED_KIND_PREFIX}{JOB_CRM_SYNC}": run_crm_sync,
    f"{SCHEDULED_KIND_PREFIX}{JOB_PURGE_EXPIRED}": run_purge_expired,
    f"{SCHEDULED_KIND_PREFIX}{JOB_BACKUP}": run_backup,
    f"{SCHEDULED_KIND_PREFIX}{JOB_BACKUP_VERIFY}": run_backup_verify,
    f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}": run_watchdog,
}
