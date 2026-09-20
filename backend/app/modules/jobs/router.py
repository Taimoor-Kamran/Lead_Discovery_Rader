"""`/search-jobs/*` and `/jobs/*` endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import CurrentUser, DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.jobs import service
from app.modules.jobs.schemas import (
    CostEstimate,
    EstimateRequest,
    IndustryOption,
    JobRunRead,
    PipelineRead,
    SearchJobCreate,
    SearchJobListItem,
    SearchJobRead,
    SearchJobUpdate,
)

search_jobs_router = APIRouter(prefix="/search-jobs", tags=["search-jobs"])
jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])

JobAuthor = Annotated[User, Depends(require_role(Role.sales_rep))]


@search_jobs_router.post("", response_model=SearchJobRead, status_code=status.HTTP_201_CREATED)
def create_search_job(
    payload: SearchJobCreate, actor: JobAuthor, session: DbSession
) -> SearchJobRead:
    job = service.create_search_job(session, payload, actor_id=actor.id)
    return SearchJobRead.model_validate(job)


@search_jobs_router.get("", response_model=Page[SearchJobListItem])
def list_search_jobs(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[SearchJobListItem]:
    """Search jobs, newest first, each with its most recent discovery run."""
    return service.list_search_jobs(session, limit=limit, cursor=cursor)


# Static paths are declared before `/{search_job_id}` so they are not read as an id.
@search_jobs_router.get("/industries", response_model=list[IndustryOption])
def list_industries(user: CurrentUser) -> list[IndustryOption]:
    """The industry dropdown: taxonomy slugs with the text a source is asked for."""
    return service.industries()


@search_jobs_router.post("/estimate", response_model=CostEstimate)
def estimate_search(
    payload: EstimateRequest, user: CurrentUser, session: DbSession
) -> CostEstimate:
    """What a run with these settings would cost against today's caps. Calls nothing."""
    return service.estimate(
        session, max_results=payload.max_results, source_ids=list(payload.source_ids)
    )


@search_jobs_router.get("/{search_job_id}", response_model=SearchJobRead)
def get_search_job(
    search_job_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> SearchJobRead:
    return SearchJobRead.model_validate(service.get_search_job(session, search_job_id))


@search_jobs_router.patch("/{search_job_id}", response_model=SearchJobRead)
def update_search_job(
    search_job_id: uuid.UUID,
    payload: SearchJobUpdate,
    actor: JobAuthor,
    session: DbSession,
) -> SearchJobRead:
    job = service.update_search_job(session, search_job_id, payload, actor_id=actor.id)
    return SearchJobRead.model_validate(job)


@search_jobs_router.get("/{search_job_id}/runs", response_model=Page[JobRunRead])
def list_search_job_runs(
    search_job_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[JobRunRead]:
    """Every run of this search job, newest first."""
    return service.list_runs_for_search_job(session, search_job_id, limit=limit, cursor=cursor)


@search_jobs_router.get("/{search_job_id}/estimate", response_model=CostEstimate)
def estimate_search_job(
    search_job_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> CostEstimate:
    """The cost estimate for running this job now."""
    return service.estimate_for_job(session, service.get_search_job(session, search_job_id))


@search_jobs_router.get("/{search_job_id}/pipeline", response_model=PipelineRead)
def search_job_pipeline(
    search_job_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    run_id: Annotated[uuid.UUID | None, Query()] = None,
) -> PipelineRead:
    """Discovery → resolution → audit → classification for the latest (or the given) run."""
    job = service.get_search_job(session, search_job_id)
    return service.pipeline(session, job, discovery_run_id=run_id)


@search_jobs_router.post(
    "/{search_job_id}/run", response_model=JobRunRead, status_code=status.HTTP_202_ACCEPTED
)
def run_search_job(
    search_job_id: uuid.UUID,
    actor: JobAuthor,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobRunRead:
    """Enqueue a run, unless today's Places cap cannot cover it (422 `daily_cap_exceeded`).
    Repeating the call with the same Idempotency-Key returns the first run."""
    job = service.get_search_job(session, search_job_id)
    run = service.run_search_job(session, job, actor_id=actor.id, idempotency_key=idempotency_key)
    return JobRunRead.model_validate(run)


@jobs_router.get("/{job_run_id}/status", response_model=JobRunRead)
def job_status(job_run_id: uuid.UUID, user: CurrentUser, session: DbSession) -> JobRunRead:
    return JobRunRead.model_validate(service.get_job_run(session, job_run_id))


@jobs_router.post("/{job_run_id}/cancel", response_model=JobRunRead)
def cancel_job(job_run_id: uuid.UUID, actor: JobAuthor, session: DbSession) -> JobRunRead:
    run = service.request_cancel(session, job_run_id, actor_id=actor.id)
    return JobRunRead.model_validate(run)
