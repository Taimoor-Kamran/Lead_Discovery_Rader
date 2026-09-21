"""The job-run state machine.

    queued  → running | cancelled | failed
    running → done | failed | cancelled | queued   (queued = a retry is scheduled)

`done`, `failed` and `cancelled` are terminal. Every accepted transition writes an audit row.

`queued → failed` exists so a run that never got to start can still be *finished*: the
worker's last-resort handler and the watchdog both use it. Without it a run that died
between being queued and being picked up stayed non-terminal for ever, and the scheduler
— which skips a job whose previous run has not finished — never queued that job again
(the v0.9.0 `scheduled:crm-sync` incident).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import InvalidStateTransitionError
from app.core.logging import get_logger
from app.modules.audit import service as audit
from app.modules.jobs.models import TERMINAL_RUN_STATUSES, JobRun, JobRunStatus

logger = get_logger("app.jobs.state")

ALLOWED_TRANSITIONS: dict[JobRunStatus, frozenset[JobRunStatus]] = {
    JobRunStatus.queued: frozenset(
        {JobRunStatus.running, JobRunStatus.cancelled, JobRunStatus.failed}
    ),
    JobRunStatus.running: frozenset(
        {
            JobRunStatus.done,
            JobRunStatus.failed,
            JobRunStatus.cancelled,
            JobRunStatus.queued,
        }
    ),
    JobRunStatus.done: frozenset(),
    JobRunStatus.failed: frozenset(),
    JobRunStatus.cancelled: frozenset(),
}


def can_transition(current: JobRunStatus, target: JobRunStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def transition(
    session: Session,
    run: JobRun,
    target: JobRunStatus,
    *,
    actor_id: uuid.UUID | None = None,
    error: str | None = None,
) -> JobRun:
    """Move a run to `target`, or raise InvalidStateTransitionError."""
    current = run.status
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            f"A job run cannot go from '{current.value}' to '{target.value}'",
            details={"from": current.value, "to": target.value, "job_run_id": str(run.id)},
        )

    now = datetime.now(UTC)
    run.status = target
    if target is JobRunStatus.running:
        run.attempts += 1
        run.error = None
        if run.started_at is None:
            run.started_at = now
    if target in TERMINAL_RUN_STATUSES:
        run.finished_at = now
    if error is not None:
        run.error = error
    session.flush()

    audit.record(
        session,
        action="job_run.status_changed",
        entity_type="job_run",
        entity_id=run.id,
        actor_id=actor_id,
        before={"status": current.value},
        after={"status": target.value, "attempts": run.attempts, "error": run.error},
    )
    logger.info(
        "job run transition",
        extra={
            "job_run_id": str(run.id),
            "from_status": current.value,
            "to_status": target.value,
            "attempts": run.attempts,
        },
    )
    return run


def backoff_seconds(attempt: int, base: int) -> int:
    """Exponential backoff: base^attempt seconds, for attempt 1, 2, 3 ..."""
    return int(base ** max(attempt, 1))
