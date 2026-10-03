"""The `audit` job: audit the businesses a resolution run touched, or just one of them.

The rule that shapes this file: **one bad website must never cost the run**. A site that
times out, serves nonsense or trips a bug in our own parsing is stored as that business's
audit and the run moves on. Only a systemic failure — the database or Redis gone — fails
the run, because that is the only kind of failure a retry can fix.
"""

import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.logging import get_logger, log_fields
from app.core.safe_fetch import CONCURRENCY_MAX_WAIT_SECONDS
from app.modules.audit import service as audit_log
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.audit_web.schemas import AuditResultSummary
from app.modules.businesses.models import Business
from app.modules.jobs.models import JobRun

logger = get_logger("app.audit_web.worker")

PARENT_RUN_PARAM = "parent_run_id"
BUSINESS_PARAM = "business_id"
THREAD_PREFIX = "audit"


def run_audits(session: Session, run: JobRun) -> None:
    """The handler registered for job kind `audit`.

    Two shapes of run arrive here, told apart by their params: a whole resolution run's
    businesses (`parent_run_id`), or one business asked for by hand (`business_id`).
    """
    if (run.params or {}).get(BUSINESS_PARAM):
        run_single_audit(session, run)
        return

    from app.workers.tasks import checkpoint

    resolution_run_id = _parent_run_id(run)
    audit_log.record(
        session,
        action="audit.run_started",
        entity_type="job_run",
        entity_id=run.id,
        after={PARENT_RUN_PARAM: str(resolution_run_id)},
    )

    businesses = service.businesses_for_run(session, resolution_run_id)
    run.progress_total = len(businesses)
    run.progress_done = 0
    checkpoint(session, run, done=0)

    config = get_settings()
    workers = config.audit_concurrency
    _warn_if_fetch_slots_run_out(config, run)
    summary = AuditResultSummary()
    tools = service.AuditTools.build(job_run_id=run.id)
    _audit_concurrently(
        session,
        run,
        [business.id for business in businesses],
        tools=tools,
        workers=workers,
        summary=summary,
    )

    run.result_summary = summary.model_dump()
    audit_log.record(
        session,
        action="audit.run_finished",
        entity_type="job_run",
        entity_id=run.id,
        after=summary.model_dump(),
    )
    session.flush()
    logger.info(
        "audit run finished",
        extra=log_fields(
            job_run_id=str(run.id),
            **{PARENT_RUN_PARAM: str(resolution_run_id)},
            **summary.model_dump(),
        ),
    )


def run_single_audit(session: Session, run: JobRun) -> None:
    """Audit exactly one business, ignoring how recently it was last audited."""
    from app.workers.tasks import checkpoint

    business_id = uuid.UUID(str((run.params or {})[BUSINESS_PARAM]))
    business = session.get(Business, business_id)
    if business is None:
        raise ValueError(f"Business {business_id} no longer exists")

    run.progress_total = 1
    checkpoint(session, run, done=0)

    summary = AuditResultSummary()
    tools = service.AuditTools.build(job_run_id=run.id)
    # Its own session, as in a run (v0.15.0): the audit commits on its own, then the run.
    _count(summary, _audit_one(business_id, tools=tools, job_run_id=run.id))

    run.result_summary = summary.model_dump()
    session.flush()
    checkpoint(session, run, done=1)
    logger.info(
        "single audit finished",
        extra=log_fields(
            job_run_id=str(run.id), business_id=str(business_id), **summary.model_dump()
        ),
    )


def _audit_concurrently(
    session: Session,
    run: JobRun,
    business_ids: list[uuid.UUID],
    *,
    tools: service.AuditTools,
    workers: int,
    summary: AuditResultSummary,
) -> None:
    """Audit the businesses with at most `workers` in flight, one thread and session each.

    Only this thread touches the run: it tallies each result as it arrives, in whatever
    order audits finish, and checkpoints after every one. A new business is handed out only
    when one finishes, so a cancellation stops the hand-out at once; the audits already in
    flight finish and are stored, then the run stops. At `workers=1` that is exactly the old
    loop: one business, a checkpoint, the next.

    Anything that escapes a worker — `RunTimedOut` in a test, a database gone — escapes here
    too, from `future.result()`. RQ's real time limit arrives on this thread instead (a
    signal handler only ever runs on the main thread), while it waits. Either way the pool
    is shut down without waiting: audits still running are not waited for past the limit,
    and the work horse's exit ends them, uncommitted, so no partial row and no "failed" row
    is written for them.
    """
    from app.workers.tasks import JobCancelledError, checkpoint

    queue = iter(business_ids)
    done = 0
    cancelled: JobCancelledError | None = None
    pool = ThreadPoolExecutor(max_workers=max(workers, 1), thread_name_prefix=THREAD_PREFIX)
    try:
        in_flight: set[Future[AuditOutcome | None]] = set()

        def hand_out() -> None:
            business_id = next(queue, None)
            if business_id is not None:
                in_flight.add(pool.submit(_audit_one, business_id, tools=tools, job_run_id=run.id))

        for _ in range(max(workers, 1)):
            hand_out()
        while in_flight:
            finished, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in finished:
                _count(summary, future.result())
                done += 1
                try:
                    checkpoint(session, run, done=done)
                except JobCancelledError as exc:
                    cancelled = cancelled or exc
                if cancelled is None:
                    hand_out()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if cancelled is not None:
        raise cancelled


@dataclass(frozen=True)
class AuditOutcome:
    """What the run's tally needs from one audit, so no ORM object crosses threads."""

    status: AuditStatus
    psi_called: bool

    @classmethod
    def of(cls, audit: WebsiteAudit) -> "AuditOutcome":
        return cls(status=audit.status, psi_called=audit.psi is not None)


def _audit_one(
    business_id: uuid.UUID,
    *,
    tools: service.AuditTools,
    job_run_id: uuid.UUID | None,
) -> AuditOutcome | None:
    """One business, in a session and transaction of its own, with its failure contained.

    The business is loaded here, by id, never handed over from another thread. A failure
    rolls back only this business's work; `record_failure` then writes the one row that
    says what happened, in the same session. `None` if the business no longer exists.
    """
    with session_scope() as session:
        business = session.get(Business, business_id)
        if business is None:
            logger.warning(
                "business vanished before its audit", extra={"business_id": str(business_id)}
            )
            return None
        try:
            audit = service.audit_business(session, business, tools=tools, job_run_id=job_run_id)
            session.commit()
            return AuditOutcome.of(audit)
        except Exception as exc:
            # The run's own time limit is `RunTimedOut`, a BaseException, so it is never
            # caught here: until v0.11.0 it was, and a killed run recorded the business it
            # was on as a failed audit and still reported done.
            session.rollback()
            logger.exception(
                "auditing one business failed", extra={"business_id": str(business_id)}
            )
            failed = service.record_failure(
                session, business, reason=f"{type(exc).__name__}: {exc}", job_run_id=job_run_id
            )
            session.commit()
            return AuditOutcome.of(failed)


def _count(summary: AuditResultSummary, outcome: AuditOutcome | None) -> None:
    if outcome is None:
        return
    status = outcome.status
    if status is AuditStatus.done:
        summary.audited += 1
    elif status is AuditStatus.skipped:
        summary.skipped += 1
    elif status is AuditStatus.robots_blocked:
        summary.robots_blocked += 1
    elif status is AuditStatus.bot_challenge:
        summary.bot_challenge += 1
    elif status is AuditStatus.not_readable:
        summary.not_readable += 1
    elif status is AuditStatus.unreachable:
        summary.unreachable += 1
    else:
        summary.failed += 1
    if outcome.psi_called:
        summary.psi_calls += 1


def _warn_if_fetch_slots_run_out(config: Settings, run: JobRun) -> None:
    """`AUDIT_CONCURRENCY` above `AUDIT_MAX_CONCURRENCY` works, but slowly (v0.15.0, C6)."""
    if config.audit_concurrency <= config.audit_max_concurrency:
        return
    logger.warning(
        "AUDIT_CONCURRENCY (%s) exceeds AUDIT_MAX_CONCURRENCY (%s): more businesses are "
        "audited at once than there are fetch slots, so fetch slots run out, and each page "
        "fetch then waits up to %g seconds for one and proceeds anyway",
        config.audit_concurrency,
        config.audit_max_concurrency,
        CONCURRENCY_MAX_WAIT_SECONDS,
        extra=log_fields(
            job_run_id=str(run.id),
            audit_concurrency=config.audit_concurrency,
            audit_max_concurrency=config.audit_max_concurrency,
        ),
    )


def _parent_run_id(run: JobRun) -> uuid.UUID:
    raw = (run.params or {}).get(PARENT_RUN_PARAM)
    if not raw:
        raise ValueError("An audit run must name the resolution run whose businesses it audits")
    return uuid.UUID(str(raw))
