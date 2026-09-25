"""RQ task entrypoints and the retry/cancel loop that wraps every job run."""

import time
import uuid
from collections.abc import Callable
from typing import Protocol

from sqlalchemy import func
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
    CLASSIFICATION_JOB_KIND,
    DEMO_JOB_KIND,
    DISCOVERY_JOB_KIND,
    RESOLUTION_JOB_KIND,
    get_job_run,
)
from app.modules.jobs.state import backoff_seconds, transition
from app.modules.opportunities.worker import run_classification
from app.modules.resolution.worker import run_resolution
from app.workers.timeouts import RunTimedOut

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
    # A heartbeat even when nothing else changed: the watchdog reads it as "still alive".
    run.updated_at = func.now()
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
register_handler(CLASSIFICATION_JOB_KIND, run_classification)

# The scheduler's jobs (v0.8.0) run through exactly the same machinery.
from app.workers.scheduled import HANDLERS as _SCHEDULED_HANDLERS  # noqa: E402

for _kind, _handler in _SCHEDULED_HANDLERS.items():
    register_handler(_kind, _handler)


def follow_up(session: Session, run: JobRun, *, dispatch: bool = True) -> None:
    """Queue whatever a finished run implies: discovery → resolution → audit → classification.

    Each key is derived from the run that triggered it, so a retried or re-requested run
    never leaves a second follow-up behind.

    `dispatch=False` creates the next run without putting it on the queue, for a caller
    walking the pipeline itself (`make load-demo-data`). The rule it serves is the one
    below: one run, one executor.
    """
    if run.kind == DISCOVERY_JOB_KIND:
        from app.modules.resolution.service import enqueue_resolution

        enqueue_resolution(
            session, run.id, idempotency_key=f"resolution:{run.id}", dispatch=dispatch
        )
        return

    if run.kind == RESOLUTION_JOB_KIND:
        from app.modules.audit_web.service import enqueue_audits_for_run

        enqueue_audits_for_run(
            session, run.id, idempotency_key=f"audit:{run.id}", dispatch=dispatch
        )
        return

    if run.kind == AUDIT_JOB_KIND:
        from app.modules.opportunities.service import enqueue_classification_for_run

        enqueue_classification_for_run(
            session, run.id, idempotency_key=f"classification:{run.id}", dispatch=dispatch
        )


class _HandlerFailedError(Exception):
    """A handler failed and its own session could not record it. Carried out to a new one."""

    def __init__(self, cause: BaseException, attempt: int) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause
        self.attempt = attempt


def _decide(
    session: Session, run: JobRun, failure: _HandlerFailedError
) -> tuple[JobRunStatus | None, int]:
    """Fail the run, or queue a retry. Returns the terminal status, or `None` and a delay."""
    settings = get_settings()
    message = str(failure)
    retryable = is_retryable(failure.cause)
    if failure.attempt >= settings.job_max_attempts or not retryable:
        transition(session, run, JobRunStatus.failed, error=message)
        logger.error(
            "job run failed permanently",
            extra={
                "job_run_id": str(run.id),
                "attempts": failure.attempt,
                "retryable": retryable,
            },
        )
        return JobRunStatus.failed, 0

    delay = backoff_seconds(failure.attempt, settings.job_backoff_base_seconds)
    transition(session, run, JobRunStatus.queued, error=message)
    logger.warning(
        "job run failed, retrying",
        extra={
            "job_run_id": str(run.id),
            "attempt": failure.attempt,
            "retry_in_seconds": delay,
        },
    )
    return None, delay


def _one_attempt(
    run_id: uuid.UUID, *, dispatch_follow_up: bool = True
) -> tuple[JobRunStatus | None, int]:
    """Run the job once. Returns its terminal status, or `None` and the seconds until a retry.

    Raises `_HandlerFailedError` only when this session could not record the outcome
    itself — an aborted transaction or a lost connection. That is decided here rather
    than outside so that a handler which wrote something before it failed (an alert, a
    partial result) keeps it: the failure and the handler's rows commit together.
    """
    with session_scope() as session:
        run = get_job_run(session, run_id)
        if run.is_terminal:
            return run.status, 0
        if run.status is JobRunStatus.running:
            # Somebody else is already running this. RQ redelivers a job when a worker
            # dies, and `make load-demo-data` used to queue a run *and* execute it, so
            # two executors claimed the same row: one of them then wrote `failed` over
            # work the other had already committed. Whoever arrives second backs off
            # here. Adjudicating a run left `running` by a dead worker belongs to the
            # watchdog, which has the stale-run limit to judge it by; an attempt that
            # has just found it cannot tell a dead worker from a live one.
            logger.warning(
                "job run is already running; leaving it to the executor that claimed it",
                extra={"job_run_id": str(run.id), "kind": run.kind},
            )
            return run.status, 0
        if run.cancel_requested and run.status is JobRunStatus.queued:
            transition(session, run, JobRunStatus.cancelled)
            return JobRunStatus.cancelled, 0

        transition(session, run, JobRunStatus.running)
        # Committed before any work starts. If the attempt dies from here on, the row on
        # disk says `running` and the watchdog owns it; without this commit the rollback
        # that follows a failure also undid the transition, putting the run back to
        # `queued` with no RQ job behind it — invisible to the watchdog and, for a
        # `scheduled:*` kind, blocking that job for ever (the v0.9.0 incident).
        session.commit()
        attempt = run.attempts

        try:
            # Looked up inside the try so an unknown kind fails the run through the
            # normal path instead of leaving it stuck as `running`.
            get_handler(run.kind)(session, run)
        except JobCancelledError as exc:
            transition(session, run, JobRunStatus.cancelled, error=str(exc))
            logger.info("job run cancelled", extra={"job_run_id": str(run.id)})
            return JobRunStatus.cancelled, 0
        except Exception as exc:  # a handler failure is data, not a crash
            failure = _HandlerFailedError(exc, attempt)
            try:
                return _decide(session, run, failure)
            except Exception:
                raise failure from exc

        transition(session, run, JobRunStatus.done)
        follow_up(session, run, dispatch=dispatch_follow_up)
        logger.info("job run done", extra={"job_run_id": str(run.id)})
        return JobRunStatus.done, 0


def _record_failure(
    run_id: uuid.UUID, failure: _HandlerFailedError
) -> tuple[JobRunStatus | None, int]:
    """Write a handler failure in a session of its own, the attempt's having gone bad."""
    with session_scope() as session:
        run = get_job_run(session, run_id)
        if run.is_terminal:
            return run.status, 0
        return _decide(session, run, failure)


def _fail_run(run_id: uuid.UUID, exc: BaseException) -> JobRunStatus:
    """The last resort: whatever broke, the run ends `failed` with the reason stored.

    Reached when the attempt itself came apart — a dead connection, an aborted
    transaction, a transition that could not be written. A brand-new session is used
    because the one that failed cannot be trusted to run another statement. Nothing a
    worker does may leave a run sitting in `running` or `queued` with no error: that is
    a run nobody will ever finish, and for a `scheduled:*` kind it stops the scheduler
    from ever queueing that job again.
    """
    message = f"{type(exc).__name__}: {exc}"
    try:
        with session_scope() as session:
            run = get_job_run(session, run_id)
            if run.is_terminal:
                return run.status
            transition(session, run, JobRunStatus.failed, error=message)
    except Exception:
        # The database is unreachable too. Say so and let RQ record the original: the
        # watchdog finishes the run once the database is back.
        logger.exception("could not record a failed job run", extra={"job_run_id": str(run_id)})
        raise exc from None
    logger.error(
        "job run failed", extra={"job_run_id": str(run_id), "error": message}, exc_info=exc
    )
    return JobRunStatus.failed


def execute_job_run(
    job_run_id: str | uuid.UUID,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    dispatch_follow_up: bool = True,
) -> JobRunStatus:
    """Run one job run to a terminal status, retrying failures with exponential backoff.

    Every exit is terminal — `done`, `cancelled` or `failed` — with one exception:
    `running`, meaning another executor holds this run and this call did nothing. Anything
    that escapes an attempt is written to the run by `_fail_run` rather than being allowed
    to leave it `running`.

    `sleeper` is injectable so tests can assert the backoff schedule without waiting.
    `dispatch_follow_up=False` keeps the next run in the chain off the queue, for a caller
    that is walking the pipeline itself.
    """
    run_id = uuid.UUID(str(job_run_id))

    while True:
        try:
            status, delay = _one_attempt(run_id, dispatch_follow_up=dispatch_follow_up)
        except RunTimedOut as exc:
            # Not retried: the same work would meet the same limit. Recorded here, while
            # RQ still gives the work horse a minute, so the run does not sit `running`
            # until the watchdog finds it; re-raised so RQ records the job as failed too.
            _fail_run(run_id, exc)
            raise
        except _HandlerFailedError as failure:
            try:
                status, delay = _record_failure(run_id, failure)
            except Exception as exc:
                return _fail_run(run_id, exc)
        except Exception as exc:
            return _fail_run(run_id, exc)

        if status is not None:
            return status
        sleeper(delay)
