"""The `resolution` job handler: turn one discovery run's records into businesses.

It runs in a single worker process against a single session, so the records of one run
are resolved one after another. That is what stops two records of the same new business
from creating two businesses: the second one's blocking query already sees the first.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.audit import service as audit
from app.modules.jobs.models import JobRun
from app.modules.resolution import service
from app.modules.resolution.schemas import ResolutionResultSummary

logger = get_logger("app.resolution.worker")

PARENT_RUN_PARAM = "parent_run_id"


def parent_run_id(run: JobRun) -> uuid.UUID:
    """Which discovery run this resolution run was asked to resolve."""
    raw = (run.params or {}).get(PARENT_RUN_PARAM)
    if not raw:
        raise ValueError("A resolution run must name the discovery run it resolves")
    return uuid.UUID(str(raw))


def run_resolution(session: Session, run: JobRun) -> None:
    """The handler registered for job kind `resolution`."""
    from app.workers.tasks import checkpoint

    source_run_id = parent_run_id(run)
    audit.record(
        session,
        action="resolution.run_started",
        entity_type="job_run",
        entity_id=run.id,
        after={PARENT_RUN_PARAM: str(source_run_id)},
    )

    records = service.records_to_resolve(session, source_run_id)
    run.progress_total = len(records)
    run.progress_done = 0
    checkpoint(session, run, done=0)

    outcomes: list[service.RecordOutcome] = []
    for record in records:
        outcomes.append(service.resolve_record(session, record))
        checkpoint(session, run, done=len(outcomes))

    summary: ResolutionResultSummary = service.summarize(outcomes)
    run.result_summary = summary.model_dump()
    audit.record(
        session,
        action="resolution.run_finished",
        entity_type="job_run",
        entity_id=run.id,
        after=summary.model_dump(),
    )
    session.flush()
    logger.info(
        "resolution finished",
        extra={
            "job_run_id": str(run.id),
            PARENT_RUN_PARAM: str(source_run_id),
            **summary.model_dump(),
        },
    )
