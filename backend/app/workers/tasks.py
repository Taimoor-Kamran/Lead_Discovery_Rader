"""RQ task entrypoints and the retry/cancel loop that wraps every job run."""

import time
import uuid
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

# Import the registry, not single models: the worker process only ever imports this
# module, and SQLAlchemy needs every table present to resolve foreign keys.
from app import models_registry  # noqa: F401
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.modules.audit_web.worker import run_audits
from app.modules.discovery.worker import run_discovery
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import (
    AUDIT_JOB_KIND,
    DEMO_JOB_KIND,
    DISCOVERY_JOB_KIND,
    RESOLUTION_JOB_KIND,
    get_job_run,
)
from app.modules.jobs.state import backoff_seconds, transition
from app.modules.resolution.worker import run_resolution

logger = get_logger("app.worker")

DEMO_DEFAULT_STEPS = 5


class JobCancelledError(Exception):
    """Raised by a handler when it notices `cancel_requested` at a progress checkpoint."""


class JobHandler(Protocol):
    def __call__(self, session: Session, run: JobRun) -> None: ...


_HANDLERS: dict[str, JobHandler] = {}


def register_handler(kind: str, handler: JobHandler) -> None:
    _HANDLERS[kind] = handler


def unregister_handler(kind: str) -> JobHandler | None:
    """Remove a handler. Used by tests to install a temporary one."""
    return _HANDLERS.pop(kind, None)


def get_handler(kind: str) -> JobHandler:
    try:
        return _HANDLERS[kind]
    except KeyError as exc:
        raise LookupError(f"No handler is registered for job kind '{kind}'") from exc


def checkpoint(session: Session, run: JobRun, *, done: int | None = None) -> None:
    """Persist progress and abort if cancellation was requested.

    Handlers must call this between units of work; it is the only place a run can be
    interrupted, which keeps cancellation predictable.
    """
    if done is not None:
        run.progress_done = done
    session.flush()
    session.commit()
    session.refresh(run)
    if run.cancel_requested:
        raise JobCancelledError(f"Job run {run.id} was cancelled at step {run.progress_done}")


def is_retryable(exc: BaseException) -> bool:
    """Whether putting a failed run back on the queue could plausibly help.

    Adapter errors carry the answer (`AuthError`, `QuotaExceededError` and `SchemaError`
    say no). Anything else is treated as transient, which is the v0.1.0 behaviour.
    """
    return bool(getattr(exc, "retryable", True))


def demo_handler(session: Session, run: JobRun) -> None:
    """A no-op unit of work, kept as the reference handler for the job framework tests."""
    steps = run.progress_total or DEMO_DEFAULT_STEPS
    run.progress_total = steps
    run.progress_done = 0
    checkpoint(session, run, done=0)
    for step in range(1, steps + 1):
        checkpoint(session, run, done=step)


register_handler(DEMO_JOB_KIND, demo_handler)
register_handler(DISCOVERY_JOB_KIND, run_discovery)
register_handler(RESOLUTION_JOB_KIND, run_resolution)
register_handler(AUDIT_JOB_KIND, run_audits)


def follow_up(session: Session, run: JobRun) -> None:
    """Queue whatever a finished run implies: discovery is resolved, resolution is audited.

    Each key is derived from the run that triggered it, so a retried or re-requested run
    never leaves a second follow-up behind.
    """
    if run.kind == DISCOVERY_JOB_KIND:
        from app.modules.resolution.service import enqueue_resolution

        enqueue_resolution(session, run.id, idempotency_key=f"resolution:{run.id}")
        return

    if run.kind == RESOLUTION_JOB_KIND:
        from app.modules.audit_web.service import enqueue_audits_for_run

        enqueue_audits_for_run(session, run.id, idempotency_key=f"audit:{run.id}")


def execute_job_run(
    job_run_id: str | uuid.UUID, *, sleeper: Callable[[float], None] = time.sleep
) -> JobRunStatus:
    """Run one job run to a terminal status, retrying failures with exponential backoff.

    `sleeper` is injectable so tests can assert the backoff schedule without waiting.
    """
    settings = get_settings()
    run_id = uuid.UUID(str(job_run_id))
    max_attempts = settings.job_max_attempts

    while True:
        with session_scope() as session:
            run = get_job_run(session, run_id)
            if run.is_terminal:
                return run.status
            if run.cancel_requested and run.status is JobRunStatus.queued:
                transition(session, run, JobRunStatus.cancelled)
                return JobRunStatus.cancelled

            transition(session, run, JobRunStatus.running)
            attempt = run.attempts

            try:
                # Looked up inside the try so an unknown kind fails the run through the
                # normal path instead of leaving it stuck as `running`.
                get_handler(run.kind)(session, run)
            except JobCancelledError as exc:
                transition(session, run, JobRunStatus.cancelled, error=str(exc))
                logger.info("job run cancelled", extra={"job_run_id": str(run.id)})
                return JobRunStatus.cancelled
            except Exception as exc:  # a handler failure is data, not a crash
                message = f"{type(exc).__name__}: {exc}"
                if attempt >= max_attempts or not is_retryable(exc):
                    transition(session, run, JobRunStatus.failed, error=message)
                    logger.error(
                        "job run failed permanently",
                        extra={
                            "job_run_id": str(run.id),
                            "attempts": attempt,
                            "retryable": is_retryable(exc),
                        },
                    )
                    return JobRunStatus.failed

                delay = backoff_seconds(attempt, settings.job_backoff_base_seconds)
                transition(session, run, JobRunStatus.queued, error=message)
                logger.warning(
                    "job run failed, retrying",
                    extra={
                        "job_run_id": str(run.id),
                        "attempt": attempt,
                        "retry_in_seconds": delay,
                    },
                )
            else:
                transition(session, run, JobRunStatus.done)
                follow_up(session, run)
                logger.info("job run done", extra={"job_run_id": str(run.id)})
                return JobRunStatus.done

        sleeper(delay)
