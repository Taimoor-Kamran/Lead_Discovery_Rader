"""`/opportunities`, `/businesses/{id}/opportunities|classify` and `/jobs/{id}/classify`."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, status

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import CurrentUser, DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.jobs.schemas import JobRunRead
from app.modules.opportunities import service
from app.modules.opportunities.models import OpportunitySource, ReviewStatus
from app.modules.opportunities.schemas import OpportunityDetail, OpportunitySummary

opportunities_router = APIRouter(prefix="/opportunities", tags=["opportunities"])
business_opportunities_router = APIRouter(prefix="/businesses", tags=["opportunities"])
job_classification_router = APIRouter(prefix="/jobs", tags=["opportunities"])

# Re-classifying spends AI budget, so it is for the roles that check the machine's work.
ClassifyRequester = Annotated[User, Depends(require_role(Role.reviewer, Role.tech_admin))]
RunOperator = Annotated[User, Depends(require_role(Role.tech_admin))]


@opportunities_router.get("", response_model=Page[OpportunitySummary])
def list_opportunities(
    user: CurrentUser,
    session: DbSession,
    service_key: Annotated[str | None, Query(alias="service")] = None,
    review_status: Annotated[ReviewStatus | None, Query()] = ReviewStatus.pending,
    min_score: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    industry: Annotated[str | None, Query()] = None,
    city: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    source: Annotated[OpportunitySource | None, Query()] = None,
    sort: Annotated[Literal["score", "created_at"], Query()] = "score",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[OpportunitySummary]:
    """Pending opportunities by default, best score first."""
    return service.list_opportunities(
        session,
        service=service_key,
        review_status=review_status,
        min_score=min_score,
        industry=industry,
        city=city,
        state=state,
        source=source,
        sort=sort,
        limit=limit,
        cursor=cursor,
    )


@opportunities_router.get("/{opportunity_id}", response_model=OpportunityDetail)
def get_opportunity(
    opportunity_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> OpportunityDetail:
    """One opportunity in full: reason, every evidence item, score components, AI provenance."""
    return service.detail(session, service.get_opportunity(session, opportunity_id))


@business_opportunities_router.get(
    "/{business_id}/opportunities", response_model=Page[OpportunitySummary]
)
def list_business_opportunities(
    business_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[OpportunitySummary]:
    """Every opportunity of one business, any review status unless one is asked for."""
    from app.modules.businesses.service import get_business

    get_business(session, business_id)
    return service.list_opportunities(
        session, business_id=business_id, review_status=review_status, limit=limit, cursor=cursor
    )


@business_opportunities_router.post(
    "/{business_id}/classify", response_model=JobRunRead, status_code=status.HTTP_202_ACCEPTED
)
def classify_business_now(
    business_id: uuid.UUID,
    actor: ClassifyRequester,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobRunRead:
    """Queue a re-classification of one business now."""
    run = service.enqueue_classification_for_business(
        session, business_id, actor_id=actor.id, idempotency_key=idempotency_key
    )
    return JobRunRead.model_validate(run)


@job_classification_router.post(
    "/{job_run_id}/classify", response_model=JobRunRead, status_code=status.HTTP_202_ACCEPTED
)
def classify_job_run(
    job_run_id: uuid.UUID,
    actor: RunOperator,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobRunRead:
    """Classify the businesses an audit run audited."""
    run = service.enqueue_classification_for_run(
        session, job_run_id, actor_id=actor.id, idempotency_key=idempotency_key
    )
    return JobRunRead.model_validate(run)
