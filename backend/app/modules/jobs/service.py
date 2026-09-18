"""Search-job CRUD, run enqueueing, cancellation and status reads."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.core.redis import get_queue
from app.modules.audit import service as audit
from app.modules.jobs.models import JobRun, JobRunStatus, SearchJob
from app.modules.jobs.schemas import (
    JobRunRead,
    SearchJobCreate,
    SearchJobRead,
    SearchJobUpdate,
)
from app.modules.jobs.state import transition

logger = get_logger("app.jobs")

DEMO_JOB_KIND = "demo"


def get_search_job(session: Session, search_job_id: uuid.UUID) -> SearchJob:
    job = session.get(SearchJob, search_job_id)
    if job is None:
        raise NotFoundError("Search job not found", details={"search_job_id": str(search_job_id)})
    return job


def create_search_job(
    session: Session, payload: SearchJobCreate, *, actor_id: uuid.UUID
) -> SearchJob:
    job = SearchJob(
        name=payload.name,
        geo=payload.geo.model_dump(exclude_none=True),
        industry=payload.industry,
        source_ids=list(payload.source_ids),
        status=payload.status,
        created_by=actor_id,
    )
    session.add(job)
    session.flush()
    audit.record(
        session,
        action="search_job.created",
        entity_type="search_job",
        entity_id=job.id,
        actor_id=actor_id,
        after=_snapshot(job),
    )
    return job


def update_search_job(
    session: Session, search_job_id: uuid.UUID, payload: SearchJobUpdate, *, actor_id: uuid.UUID
) -> SearchJob:
    job = get_search_job(session, search_job_id)
    before = _snapshot(job)

    if payload.name is not None:
        job.name = payload.name
    if payload.geo is not None:
        job.geo = payload.geo.model_dump(exclude_none=True)
    if payload.industry is not None:
        job.industry = payload.industry
    if payload.source_ids is not None:
        job.source_ids = list(payload.source_ids)
    if payload.status is not None:
        job.status = payload.status
    session.flush()

    audit.record(
        session,
        action="search_job.updated",
        entity_type="search_job",
        entity_id=job.id,
        actor_id=actor_id,
        before=before,
        after=_snapshot(job),
    )
    return job


def list_search_jobs(
    session: Session, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> Page[SearchJobRead]:
    stmt = (
        select(SearchJob)
        .order_by(SearchJob.created_at.desc(), SearchJob.id.desc())
        .limit(limit + 1)
    )
    stmt = apply_cursor(stmt, SearchJob.created_at, SearchJob.id, cursor)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[SearchJobRead](
        items=[SearchJobRead.model_validate(row) for row in rows], next_cursor=next_cursor
    )


def get_job_run(session: Session, job_run_id: uuid.UUID) -> JobRun:
    run = session.get(JobRun, job_run_id)
    if run is None:
        raise NotFoundError("Job run not found", details={"job_run_id": str(job_run_id)})
    return run


def enqueue_run(
    session: Session,
    *,
    search_job_id: uuid.UUID | None,
    kind: str = DEMO_JOB_KIND,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    progress_total: int = 0,
) -> JobRun:
    """Create a `queued` job run and hand it to RQ.

    An `idempotency_key` that has been used before returns the original run instead of
    creating a second one.
    """
    if idempotency_key:
        existing = session.scalars(
            select(JobRun).where(JobRun.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            logger.info(
                "run request deduplicated",
                extra={"job_run_id": str(existing.id), "kind": existing.kind},
            )
            return existing

    run = JobRun(
        search_job_id=search_job_id,
        kind=kind,
        status=JobRunStatus.queued,
        progress_total=progress_total,
        idempotency_key=idempotency_key,
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        duplicate = session.scalars(
            select(JobRun).where(JobRun.idempotency_key == idempotency_key)
        ).first()
        if duplicate is not None:
            return duplicate
        raise ConflictError("Could not create the job run") from exc

    audit.record(
        session,
        action="job_run.enqueued",
        entity_type="job_run",
        entity_id=run.id,
        actor_id=actor_id,
        after={"kind": kind, "search_job_id": str(search_job_id) if search_job_id else None},
    )
    session.commit()

    # Import here: the worker entrypoint imports this module, so a module-level import
    # would be circular.
    from app.workers.tasks import execute_job_run

    get_queue().enqueue(execute_job_run, str(run.id), job_id=str(run.id))
    logger.info("job run enqueued", extra={"job_run_id": str(run.id), "kind": kind})
    return run


def request_cancel(
    session: Session, job_run_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> JobRun:
    """Flag a run for cancellation.

    A `queued` run is cancelled straight away. A `running` run is flagged; the worker sees
    the flag at its next progress checkpoint and stops. A terminal run is left alone.
    """
    run = get_job_run(session, job_run_id)
    if run.is_terminal:
        return run

    if not run.cancel_requested:
        run.cancel_requested = True
        session.flush()
        audit.record(
            session,
            action="job_run.cancel_requested",
            entity_type="job_run",
            entity_id=run.id,
            actor_id=actor_id,
            after={"status": run.status.value},
        )

    if run.status is JobRunStatus.queued:
        transition(session, run, JobRunStatus.cancelled, actor_id=actor_id)
    return run


def read_run(run: JobRun) -> JobRunRead:
    return JobRunRead.model_validate(run)


def _snapshot(job: SearchJob) -> dict[str, Any]:
    return {
        "name": job.name,
        "geo": job.geo,
        "industry": job.industry,
        "source_ids": [str(s) for s in job.source_ids],
        "status": job.status.value,
    }


def max_attempts() -> int:
    return get_settings().job_max_attempts
