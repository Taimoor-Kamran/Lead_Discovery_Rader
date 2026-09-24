"""Search-job CRUD, run enqueueing, cancellation and status reads."""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.core.redis import get_queue
from app.modules.audit import service as audit
from app.modules.jobs.models import JobRun, JobRunStatus, SearchJob
from app.modules.jobs.schemas import (
    CostEstimate,
    IndustryOption,
    JobRunRead,
    PipelineRead,
    PipelineStage,
    SearchJobCreate,
    SearchJobListItem,
    SearchJobUpdate,
)
from app.modules.jobs.state import transition
from app.modules.sources import service as sources_service

logger = get_logger("app.jobs")

DEMO_JOB_KIND = "demo"
DISCOVERY_JOB_KIND = "discovery"
RESOLUTION_JOB_KIND = "resolution"
AUDIT_JOB_KIND = "audit"
CLASSIFICATION_JOB_KIND = "classification"
# Operator-run twins of the scheduled backup jobs (`make backup`, `make backup-verify`).
BACKUP_JOB_KIND = "backup"
BACKUP_VERIFY_JOB_KIND = "backup-verify"


def get_search_job(session: Session, search_job_id: uuid.UUID) -> SearchJob:
    job = session.get(SearchJob, search_job_id)
    if job is None:
        raise NotFoundError("Search job not found", details={"search_job_id": str(search_job_id)})
    return job


def validate_max_results(value: int | None) -> int | None:
    """A search job may ask for fewer results than the cap, never more."""
    cap = get_settings().places_max_results_per_job
    if value is not None and value > cap:
        raise ValidationFailedError(
            f"max_results may not exceed PLACES_MAX_RESULTS_PER_JOB ({cap})",
            details={"max_results": value, "cap": cap},
        )
    return value


def effective_max_results(job: SearchJob) -> int:
    cap = get_settings().places_max_results_per_job
    return min(job.max_results, cap) if job.max_results else cap


def create_search_job(
    session: Session, payload: SearchJobCreate, *, actor_id: uuid.UUID
) -> SearchJob:
    sources_service.validate_source_ids(session, list(payload.source_ids))
    job = SearchJob(
        name=payload.name,
        geo=payload.geo.model_dump(exclude_none=True),
        industry=payload.industry,
        source_ids=list(payload.source_ids),
        status=payload.status,
        max_results=validate_max_results(payload.max_results),
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
        sources_service.validate_source_ids(session, list(payload.source_ids))
        job.source_ids = list(payload.source_ids)
    if payload.status is not None:
        job.status = payload.status
    if payload.max_results is not None:
        job.max_results = validate_max_results(payload.max_results)
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
) -> Page[SearchJobListItem]:
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
    return Page[SearchJobListItem](
        items=[
            SearchJobListItem.model_validate(row).model_copy(
                update={"last_run": _read_optional(latest_discovery_run(session, row.id))}
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


def _read_optional(run: JobRun | None) -> JobRunRead | None:
    return JobRunRead.model_validate(run) if run is not None else None


def latest_discovery_run(session: Session, search_job_id: uuid.UUID) -> JobRun | None:
    return session.scalars(
        select(JobRun)
        .where(JobRun.search_job_id == search_job_id, JobRun.kind == DISCOVERY_JOB_KIND)
        .order_by(JobRun.created_at.desc(), JobRun.id.desc())
        .limit(1)
    ).first()


# --- the searches page: industries, cost estimate, pipeline view ---------------------------


def industries() -> list[IndustryOption]:
    """The dropdown for a new search: one entry per industry slug in the taxonomy, with
    the Places category name as the text the source is asked for."""
    from app.modules.normalization.taxonomy import PLACES_TYPE_TO_INDUSTRY, TEXT_TO_INDUSTRY

    query_for: dict[str, str] = {}
    for places_type, slug in PLACES_TYPE_TO_INDUSTRY.items():
        query_for.setdefault(slug, places_type.replace("_", " "))
    for word, slug in TEXT_TO_INDUSTRY.items():
        query_for.setdefault(slug, word)
    return sorted(
        (
            IndustryOption(key=slug, label=slug.replace("_", " ").capitalize(), query=query)
            for slug, query in query_for.items()
        ),
        key=lambda option: option.label,
    )


def _uses_places(session: Session, source_ids: list[uuid.UUID]) -> bool:
    """Whether the run would call Google Places. No sources = the default source."""
    from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES

    if not source_ids:
        return True
    names = {
        source.name for source in sources_service.validate_source_ids(session, list(source_ids))
    }
    return GOOGLE_PLACES in names


def estimate(
    session: Session, *, max_results: int | None, source_ids: list[uuid.UUID]
) -> CostEstimate:
    """What one run would cost against today's caps and budget. Nothing is called."""
    from app.core.ratelimit import DailyCallCap
    from app.core.redis import get_redis
    from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
    from app.modules.adapters.google_places.adapter import effective_limit, run_call_ceiling
    from app.modules.ai.budget import AIBudget
    from app.modules.audit_web.psi import PAGESPEED_SOURCE_NAME

    settings = get_settings()
    wanted = validate_max_results(max_results) or settings.places_max_results_per_job
    uses_places = _uses_places(session, source_ids)
    redis = get_redis()

    places_used = DailyCallCap(
        redis, source=GOOGLE_PLACES, cap=settings.places_daily_call_cap
    ).used()
    places_remaining = max(settings.places_daily_call_cap - places_used, 0)
    # The enforced ceiling, not a guess: Places page sizes vary between identical requests,
    # so no exact count can be promised, but the run can never spend more than this.
    places_max_calls = run_call_ceiling(effective_limit(wanted)) if uses_places else 0

    psi_used = DailyCallCap(
        redis, source=PAGESPEED_SOURCE_NAME, cap=settings.psi_daily_call_cap
    ).used()
    psi_remaining = max(settings.psi_daily_call_cap - psi_used, 0)

    budget = AIBudget(redis, settings=settings).status()
    ai_enabled = settings.ai_enabled
    ai_calls = wanted if ai_enabled else 0

    blockers: list[str] = []
    if places_max_calls > places_remaining:
        blockers.append(
            f"This run may make up to {places_max_calls} Google Places call(s) but only "
            f"{places_remaining} of today's {settings.places_daily_call_cap} remain. "
            "Wait for UTC midnight, lower the results, or raise PLACES_DAILY_CALL_CAP."
        )
    return CostEstimate(
        max_results=wanted,
        uses_places=uses_places,
        places_max_calls=places_max_calls,
        places_used_today=places_used,
        places_daily_cap=settings.places_daily_call_cap,
        places_remaining_today=places_remaining,
        pagespeed_calls=wanted,
        pagespeed_used_today=psi_used,
        pagespeed_daily_cap=settings.psi_daily_call_cap,
        pagespeed_remaining_today=psi_remaining,
        ai_enabled=ai_enabled,
        ai_provider=settings.resolved_ai_provider,
        ai_calls=ai_calls,
        ai_calls_used_today=budget.calls,
        ai_daily_call_cap=budget.call_cap,
        ai_budget_usd=float(budget.budget_usd),
        ai_spent_today_usd=float(budget.spent_usd),
        ai_budget_remaining_usd=float(budget.remaining_usd),
        can_run=not blockers,
        blockers=blockers,
    )


def estimate_for_job(session: Session, job: SearchJob) -> CostEstimate:
    return estimate(session, max_results=job.max_results, source_ids=list(job.source_ids))


def run_search_job(
    session: Session,
    job: SearchJob,
    *,
    actor_id: uuid.UUID,
    idempotency_key: str | None = None,
) -> JobRun:
    """Queue a discovery run, unless today's Places cap could not cover it."""
    cost = estimate_for_job(session, job)
    if not cost.can_run:
        raise ValidationFailedError(
            cost.blockers[0],
            details={"estimate": cost.model_dump()},
            code="daily_cap_exceeded",
        )
    return enqueue_run(
        session, search_job_id=job.id, actor_id=actor_id, idempotency_key=idempotency_key
    )


PIPELINE_STAGES = (
    DISCOVERY_JOB_KIND,
    RESOLUTION_JOB_KIND,
    AUDIT_JOB_KIND,
    CLASSIFICATION_JOB_KIND,
)


def _child_run(session: Session, parent: JobRun, kind: str) -> JobRun | None:
    return session.scalars(
        select(JobRun)
        .where(JobRun.kind == kind, JobRun.params["parent_run_id"].astext == str(parent.id))
        .order_by(JobRun.created_at.desc(), JobRun.id.desc())
        .limit(1)
    ).first()


def pipeline(
    session: Session, job: SearchJob, *, discovery_run_id: uuid.UUID | None = None
) -> PipelineRead:
    """Discovery → resolution → audit → classification for one run of the job.

    Each follow-up run carries `params.parent_run_id`, which is how the chain is walked.
    A stage that has not been queued yet reads as `run = null`.
    """
    if discovery_run_id is not None:
        first: JobRun | None = get_job_run(session, discovery_run_id)
        if first is None or first.search_job_id != job.id or first.kind != DISCOVERY_JOB_KIND:
            raise NotFoundError(
                "That run is not a discovery run of this search",
                details={"job_run_id": str(discovery_run_id)},
            )
    else:
        first = latest_discovery_run(session, job.id)

    stages: list[PipelineStage] = []
    current = first
    for kind in PIPELINE_STAGES:
        if kind != DISCOVERY_JOB_KIND and current is not None:
            current = _child_run(session, current, kind)
        stages.append(
            PipelineStage(
                stage=kind,
                run=_read_optional(current),
                counts=dict(current.result_summary or {}) if current else {},
                error=current.error if current else None,
            )
        )
    query = {"city": str(job.geo.get("city"))} if job.geo.get("city") else {}
    return PipelineRead(
        search_job_id=job.id,
        discovery_run_id=first.id if first else None,
        stages=stages,
        review_queue_query=query,
    )


def list_runs_for_search_job(
    session: Session,
    search_job_id: uuid.UUID,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[JobRunRead]:
    """Runs of one search job, newest first."""
    get_search_job(session, search_job_id)
    stmt = (
        select(JobRun)
        .where(JobRun.search_job_id == search_job_id)
        .order_by(JobRun.created_at.desc(), JobRun.id.desc())
        .limit(limit + 1)
    )
    stmt = apply_cursor(stmt, JobRun.created_at, JobRun.id, cursor)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[JobRunRead](
        items=[JobRunRead.model_validate(row) for row in rows], next_cursor=next_cursor
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
    kind: str = DISCOVERY_JOB_KIND,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    progress_total: int = 0,
    params: dict[str, Any] | None = None,
    dispatch: bool = True,
) -> JobRun:
    """Create a `queued` job run and hand it to RQ.

    An `idempotency_key` that has been used before returns the original run instead of
    creating a second one.

    `dispatch=False` creates the run but does **not** put it on the queue, for a caller
    that is going to execute it itself (the demo commands). A run must have exactly one
    executor: queue it *and* run it and two processes race over the same row, which is
    what broke `make e2e` and what produced the duplicate-opportunity errors in
    `logs/worker.log` (spec v0.9.0).
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
        params=params,
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
        after={
            "kind": kind,
            "search_job_id": str(search_job_id) if search_job_id else None,
            "params": params,
        },
    )
    session.commit()

    if not dispatch:
        logger.info(
            "job run created for its caller to execute",
            extra={"job_run_id": str(run.id), "kind": kind},
        )
        return run

    # Import here: the worker entrypoint imports this module, so a module-level import
    # would be circular.
    from app.workers.tasks import execute_job_run

    get_queue().enqueue(
        execute_job_run,
        str(run.id),
        job_id=str(run.id),
        job_timeout=run_time_limit_seconds(session, run),
    )
    logger.info("job run enqueued", extra={"job_run_id": str(run.id), "kind": kind})
    return run


def run_time_limit_seconds(session: Session, run: JobRun) -> int:
    """The time limit RQ holds this run to — never RQ's own default of 180 s.

    An audit run is sized by the businesses it will audit, one at a time; everything else
    gets `JOB_TIMEOUT_SECONDS`. The limit covers the whole job, retries included. Reaching
    it fails the run as a timeout (`RunTimedOut`), never as one business's failed audit.
    """
    settings = get_settings()
    limit = settings.job_timeout_seconds
    parent = (run.params or {}).get("parent_run_id")
    if run.kind == AUDIT_JOB_KIND and parent:
        from app.modules.audit_web.service import businesses_for_run

        count = len(businesses_for_run(session, uuid.UUID(str(parent))))
        limit = max(limit, count * settings.audit_seconds_per_business)
    return limit


def run_inline(
    session: Session,
    *,
    kind: str,
    work: Callable[[Session, JobRun], dict[str, Any] | None],
    actor_id: uuid.UUID | None = None,
    params: dict[str, Any] | None = None,
) -> JobRun:
    """Create a job run and execute `work` in this process, right now.

    For operator commands (`make backup`, `make backup-verify`) that must be visible on
    the health page like their scheduled twins but have no worker in the loop. The run
    ends `done` with `work`'s return value as its summary, or `failed` with the error
    message; a failure is recorded, never raised past here.
    """
    run = JobRun(kind=kind, status=JobRunStatus.queued, params=params)
    session.add(run)
    session.flush()
    audit.record(
        session,
        action="job_run.enqueued",
        entity_type="job_run",
        entity_id=run.id,
        actor_id=actor_id,
        after={"kind": kind, "search_job_id": None, "params": params, "inline": True},
    )
    transition(session, run, JobRunStatus.running, actor_id=actor_id)
    session.commit()
    try:
        run.result_summary = work(session, run)
    except Exception as exc:  # the failure is the result
        transition(
            session,
            run,
            JobRunStatus.failed,
            actor_id=actor_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.error("inline job run failed", extra={"job_run_id": str(run.id), "kind": kind})
    else:
        transition(session, run, JobRunStatus.done, actor_id=actor_id)
    session.commit()
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
        "max_results": job.max_results,
    }


def max_attempts() -> int:
    return get_settings().job_max_attempts
