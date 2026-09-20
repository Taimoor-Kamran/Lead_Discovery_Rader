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

WORKER_LOST = "worker lost: the run was still `running` after the stale-run limit"


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
    """Runs still `running` after the configured limit — a worker died under them."""
    limit = now - timedelta(minutes=get_settings().watchdog_stale_minutes)
    stmt = select(JobRun).where(
        JobRun.status == JobRunStatus.running,
        JobRun.started_at.is_not(None),
        JobRun.started_at < limit,
    )
    if exclude is not None:
        stmt = stmt.where(JobRun.id != exclude.id)
    return list(session.scalars(stmt.order_by(JobRun.started_at.asc())))


def run_watchdog(session: Session, run: JobRun, *, now: datetime | None = None) -> None:
    """Fail abandoned runs with "worker lost", alert, then re-evaluate every alert rule."""
    from app.modules.alerts import service as alerts

    moment = now or datetime.now(UTC)
    failed: list[dict[str, Any]] = []
    for stale in stale_runs(session, now=moment, exclude=run):
        transition(session, stale, JobRunStatus.failed, error=WORKER_LOST)
        failed.append({"job_run_id": str(stale.id), "kind": stale.kind})
        alerts.raise_alert(
            session,
            alerts.RULE_STALE_JOB,
            f"A {stale.kind} run was stuck in `running` for more than "
            f"{get_settings().watchdog_stale_minutes} minutes and was failed (worker lost)",
            details={"job_run_id": str(stale.id), "kind": stale.kind},
            now=moment,
        )
        logger.warning(
            "stale run failed by the watchdog",
            extra={"job_run_id": str(stale.id), "kind": stale.kind},
        )

    from app.modules.monitoring import service as monitoring

    evaluated = monitoring.evaluate_alerts(session, now=moment)
    run.result_summary = {
        "stale_runs_failed": len(failed),
        "stale": failed,
        "alerts_open": len(evaluated),
    }


HANDLERS = {
    f"{SCHEDULED_KIND_PREFIX}{JOB_CRM_SYNC}": run_crm_sync,
    f"{SCHEDULED_KIND_PREFIX}{JOB_PURGE_EXPIRED}": run_purge_expired,
    f"{SCHEDULED_KIND_PREFIX}{JOB_BACKUP}": run_backup,
    f"{SCHEDULED_KIND_PREFIX}{JOB_BACKUP_VERIFY}": run_backup_verify,
    f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}": run_watchdog,
}
