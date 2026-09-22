"""`/review-queue`, `/opportunities/{id}/review`, `/opportunities/review-batch`,
`/review-decisions/{id}/undo` and `/leads`."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.opportunities.models import ReviewStatus
from app.modules.review import service
from app.modules.review.schemas import (
    BatchReviewRequest,
    BatchReviewResult,
    DecidedOpportunity,
    LeadDetail,
    LeadRead,
    QueueItem,
    ReviewDetail,
    ReviewRequest,
    UndoResult,
)

review_queue_router = APIRouter(prefix="/review-queue", tags=["review"])
review_router = APIRouter(prefix="/opportunities", tags=["review"])
decisions_router = APIRouter(prefix="/review-decisions", tags=["review"])
leads_router = APIRouter(prefix="/leads", tags=["review"])

# The widest "found within" window either list accepts: ten years, which is a bound on the
# parameter rather than a product decision. The UI offers 1, 3, 7 and 30 days.
MAX_RECENCY_DAYS = 3650

# Blueprint slide 7. Reading the queue: everyone who checks or exports the machine's work.
# Deciding: reviewers (and admins, who pass every `require_role`). Sales reps see leads.
QueueReader = Annotated[
    User, Depends(require_role(Role.reviewer, Role.crm_manager, Role.tech_admin))
]
Reviewer = Annotated[User, Depends(require_role(Role.reviewer))]
LeadReader = Annotated[User, Depends(require_role(Role.reviewer, Role.sales_rep, Role.crm_manager))]


@review_queue_router.get("", response_model=Page[QueueItem])
def review_queue(
    actor: QueueReader,
    session: DbSession,
    status: Annotated[ReviewStatus, Query()] = ReviewStatus.pending,
    service_key: Annotated[str | None, Query(alias="service")] = None,
    city: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    industry: Annotated[str | None, Query()] = None,
    min_score: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    include_weak: Annotated[bool, Query()] = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
    discovered_within_days: Annotated[int | None, Query(ge=1, le=MAX_RECENCY_DAYS)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[QueueItem]:
    """Businesses with open opportunities, best score first. Weak signals hidden by default.

    `discovered_within_days=N` keeps only businesses **first found within N days** — the
    earliest `discovered_at` of the records behind the business, not the last time a source
    handed the same record over again. It composes with every other filter with AND, and is
    re-applied on each page, so a cursor never widens the window.
    """
    return service.review_queue(
        session,
        status=status,
        service=service_key,
        city=city,
        state=state,
        industry=industry,
        min_score=min_score,
        include_weak=include_weak,
        q=q,
        discovered_within_days=discovered_within_days,
        limit=limit,
        cursor=cursor,
    )


@review_queue_router.get("/{business_id}", response_model=ReviewDetail)
def review_detail(business_id: uuid.UUID, actor: QueueReader, session: DbSession) -> ReviewDetail:
    """Facts with provenance, the latest audit, the AI summary, every opportunity, history."""
    return service.review_detail(session, business_id, actor=actor)


@review_router.post("/review-batch", response_model=BatchReviewResult)
def review_batch(
    payload: BatchReviewRequest, actor: Reviewer, session: DbSession
) -> BatchReviewResult:
    """Reject or not-a-fit up to 50 opportunities. Approvals are never batched."""
    return service.decide_batch(session, payload, actor=actor)


@review_router.post("/{opportunity_id}/review", response_model=DecidedOpportunity)
def review_opportunity(
    opportunity_id: uuid.UUID, payload: ReviewRequest, actor: Reviewer, session: DbSession
) -> DecidedOpportunity:
    """One decision on one opportunity. 409 when someone else already decided it."""
    return service.decide(session, opportunity_id, payload, actor=actor)


@decisions_router.post("/{decision_id}/undo", response_model=UndoResult)
def undo_decision(decision_id: uuid.UUID, actor: Reviewer, session: DbSession) -> UndoResult:
    """Within the window, by the person who decided or an admin."""
    return service.undo(session, decision_id, actor=actor)


@leads_router.get("", response_model=Page[LeadRead])
def list_leads(
    actor: LeadReader,
    session: DbSession,
    service_key: Annotated[str | None, Query(alias="service")] = None,
    assigned_to: Annotated[uuid.UUID | None, Query()] = None,
    city: Annotated[str | None, Query()] = None,
    discovered_within_days: Annotated[int | None, Query(ge=1, le=MAX_RECENCY_DAYS)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[LeadRead]:
    """Approved, unsuppressed opportunities. A sales rep sees only their own.

    `discovered_within_days=N` keeps only businesses **first found within N days** — the
    earliest `discovered_at` of the records behind the business, not the last time a source
    handed the same record over again. It composes with every other filter with AND, and is
    re-applied on each page, so a cursor never widens the window.
    """
    return service.list_leads(
        session,
        actor=actor,
        service=service_key,
        assigned_to=assigned_to,
        city=city,
        discovered_within_days=discovered_within_days,
        limit=limit,
        cursor=cursor,
    )


@leads_router.get("/{opportunity_id}", response_model=LeadDetail)
def lead_detail(opportunity_id: uuid.UUID, actor: LeadReader, session: DbSession) -> LeadDetail:
    """One lead, read-only. A sales rep gets 403 on a lead not assigned to them."""
    return service.lead_detail(session, opportunity_id, actor=actor)
