"""The `audit` job: audit the businesses a resolution run touched, or just one of them.

The rule that shapes this file: **one bad website must never cost the run**. A site that
times out, serves nonsense or trips a bug in our own parsing is stored as that business's
audit and the run moves on. Only a systemic failure — the database or Redis gone — fails
the run, because that is the only kind of failure a retry can fix.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_fields
from app.modules.audit import service as audit_log
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.audit_web.schemas import AuditResultSummary
from app.modules.businesses.models import Business
from app.modules.jobs.models import JobRun

logger = get_logger("app.audit_web.worker")

PARENT_RUN_PARAM = "parent_run_id"
BUSINESS_PARAM = "business_id"


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

    summary = AuditResultSummary()
    tools = service.AuditTools.build(job_run_id=run.id)
    for index, business in enumerate(businesses, start=1):
        _count(summary, _audit_one(session, business, tools=tools, job_run_id=run.id))
        # Also where a cancellation takes effect, so a long audit run stops between
        # businesses rather than halfway through one.
        checkpoint(session, run, done=index)

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
    _count(summary, _audit_one(session, business, tools=tools, job_run_id=run.id))

    run.result_summary = summary.model_dump()
    session.flush()
    checkpoint(session, run, done=1)
    logger.info(
        "single audit finished",
        extra=log_fields(
            job_run_id=str(run.id), business_id=str(business_id), **summary.model_dump()
        ),
    )


def _audit_one(
    session: Session,
    business: Business,
    *,
    tools: service.AuditTools,
    job_run_id: uuid.UUID | None,
) -> WebsiteAudit:
    """One business, with its own failure contained.

    The rollback matters: a half-written audit must not poison the session the rest of the
    run shares. `record_failure` then writes the one row that says what happened.
    """
    savepoint = session.begin_nested()
    try:
        audit = service.audit_business(session, business, tools=tools, job_run_id=job_run_id)
        savepoint.commit()
        return audit
    except Exception as exc:
        # The run's own time limit is `RunTimedOut`, a BaseException, so it is never
        # caught here: until v0.11.0 it was, and a killed run recorded the business it
        # was on as a failed audit and still reported done.
        savepoint.rollback()
        logger.exception("auditing one business failed", extra={"business_id": str(business.id)})
        return service.record_failure(
            session, business, reason=f"{type(exc).__name__}: {exc}", job_run_id=job_run_id
        )


def _count(summary: AuditResultSummary, audit: WebsiteAudit) -> None:
    if audit.status is AuditStatus.done:
        summary.audited += 1
    elif audit.status is AuditStatus.skipped:
        summary.skipped += 1
    elif audit.status is AuditStatus.robots_blocked:
        summary.robots_blocked += 1
    elif audit.status is AuditStatus.unreachable:
        summary.unreachable += 1
    else:
        summary.failed += 1
    if audit.psi is not None:
        summary.psi_calls += 1


def _parent_run_id(run: JobRun) -> uuid.UUID:
    raw = (run.params or {}).get(PARENT_RUN_PARAM)
    if not raw:
        raise ValueError("An audit run must name the resolution run whose businesses it audits")
    return uuid.UUID(str(raw))
