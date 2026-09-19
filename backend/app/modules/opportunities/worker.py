"""The `classification` job: classify the businesses an audit run audited, or just one.

Same rule as the audit worker: **one business must never cost the run**. Each business is
classified inside its own savepoint; a failure is logged, rolled back and counted, and the
run carries on. The AI has a second layer of the same protection inside `service.classify`:
a model that errors, drifts or exceeds the budget leaves the rule opportunities standing.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_fields
from app.modules.audit import service as audit_log
from app.modules.businesses.models import Business
from app.modules.jobs.models import JobRun
from app.modules.opportunities import service
from app.modules.opportunities.schemas import ClassificationResultSummary

logger = get_logger("app.opportunities.worker")

PARENT_RUN_PARAM = "parent_run_id"
BUSINESS_PARAM = "business_id"


def run_classification(session: Session, run: JobRun) -> None:
    """The handler registered for job kind `classification`.

    Two shapes of run, told apart by their params: a whole audit run's businesses
    (`parent_run_id`), or one business asked for by hand (`business_id`).
    """
    if (run.params or {}).get(BUSINESS_PARAM):
        classify_business(session, run)
        return

    from app.workers.tasks import checkpoint

    audit_run_id = _parent_run_id(run)
    audit_log.record(
        session,
        action="classification.run_started",
        entity_type="job_run",
        entity_id=run.id,
        after={PARENT_RUN_PARAM: str(audit_run_id)},
    )

    businesses = service.businesses_for_run(session, audit_run_id)
    run.progress_total = len(businesses)
    run.progress_done = 0
    checkpoint(session, run, done=0)

    summary = ClassificationResultSummary()
    tools = service.ClassificationTools.build(job_run_id=run.id)
    for index, business in enumerate(businesses, start=1):
        _count(summary, _classify_one(session, business, tools=tools, job_run_id=run.id))
        checkpoint(session, run, done=index)

    run.result_summary = summary.model_dump()
    audit_log.record(
        session,
        action="classification.run_finished",
        entity_type="job_run",
        entity_id=run.id,
        after=summary.model_dump(),
    )
    session.flush()
    logger.info(
        "classification run finished",
        extra=log_fields(
            job_run_id=str(run.id),
            **{PARENT_RUN_PARAM: str(audit_run_id)},
            **summary.model_dump(),
        ),
    )


def classify_business(session: Session, run: JobRun) -> None:
    """Classify exactly one business now."""
    from app.workers.tasks import checkpoint

    business_id = uuid.UUID(str((run.params or {})[BUSINESS_PARAM]))
    business = session.get(Business, business_id)
    if business is None:
        raise ValueError(f"Business {business_id} no longer exists")

    run.progress_total = 1
    checkpoint(session, run, done=0)

    summary = ClassificationResultSummary()
    tools = service.ClassificationTools.build(job_run_id=run.id)
    _count(summary, _classify_one(session, business, tools=tools, job_run_id=run.id))

    run.result_summary = summary.model_dump()
    session.flush()
    checkpoint(session, run, done=1)
    logger.info(
        "single classification finished",
        extra=log_fields(
            job_run_id=str(run.id), business_id=str(business_id), **summary.model_dump()
        ),
    )


def _classify_one(
    session: Session,
    business: Business,
    *,
    tools: service.ClassificationTools,
    job_run_id: uuid.UUID | None,
) -> service.ClassificationOutcome | None:
    """One business, with its own failure contained in a savepoint."""
    savepoint = session.begin_nested()
    try:
        outcome = service.classify(session, business, tools=tools, job_run_id=job_run_id)
        savepoint.commit()
        return outcome
    except Exception:
        savepoint.rollback()
        logger.exception("classifying one business failed", extra={"business_id": str(business.id)})
        return None


def _count(
    summary: ClassificationResultSummary, outcome: service.ClassificationOutcome | None
) -> None:
    summary.businesses += 1
    if outcome is None:
        summary.ai_errors += 1
        return
    summary.opportunities_created += outcome.created
    summary.opportunities_updated += outcome.updated
    ai = outcome.ai
    if ai is None:
        return
    summary.ai_calls += ai.calls
    summary.ai_escalations += 1 if ai.escalated else 0
    summary.ai_reused += ai.reused
    summary.ai_skipped_budget += ai.skipped_budget
    summary.ai_errors += ai.errors
    summary.est_cost_usd = round(summary.est_cost_usd + float(ai.cost), 6)


def _parent_run_id(run: JobRun) -> uuid.UUID:
    raw = (run.params or {}).get(PARENT_RUN_PARAM)
    if not raw:
        raise ValueError("A classification run must name the audit run whose businesses it scores")
    return uuid.UUID(str(raw))
