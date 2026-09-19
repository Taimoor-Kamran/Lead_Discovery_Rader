"""`/match-candidates/*`: the review queue for pairs a machine would not decide."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.jobs.schemas import JobRunRead
from app.modules.resolution import service
from app.modules.resolution.models import MatchCandidateStatus
from app.modules.resolution.schemas import MatchCandidateDetail, MatchDecisionRequest

match_candidates_router = APIRouter(prefix="/match-candidates", tags=["resolution"])
job_resolution_router = APIRouter(prefix="/jobs", tags=["resolution"])

# Deciding whether two records are one business is the human gate this whole module
# exists for, so it is limited to the roles whose job that is.
Reviewer = Annotated[User, Depends(require_role(Role.reviewer))]
RunOperator = Annotated[User, Depends(require_role(Role.tech_admin))]


@match_candidates_router.get("", response_model=Page[MatchCandidateDetail])
def list_match_candidates(
    actor: Reviewer,
    session: DbSession,
    status: Annotated[MatchCandidateStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[MatchCandidateDetail]:
    return service.list_candidates(session, status=status, limit=limit, cursor=cursor)


@match_candidates_router.post("/{candidate_id}/decision", response_model=MatchCandidateDetail)
def decide_match_candidate(
    candidate_id: uuid.UUID,
    payload: MatchDecisionRequest,
    actor: Reviewer,
    session: DbSession,
) -> MatchCandidateDetail:
    """`merge` links the record to that business; `keep_apart` may create a new one."""
    candidate = service.decide_candidate(
        session, candidate_id, decision=payload.decision, actor_id=actor.id
    )
    return service.detail(session, candidate)


@job_resolution_router.post(
    "/{job_run_id}/resolve", response_model=JobRunRead, status_code=status.HTTP_202_ACCEPTED
)
def resolve_job_run(
    job_run_id: uuid.UUID,
    actor: RunOperator,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobRunRead:
    """Resolve a discovery run again by hand. Resolution is a no-op when nothing changed."""
    run = service.enqueue_resolution(
        session, job_run_id, actor_id=actor.id, idempotency_key=idempotency_key
    )
    return JobRunRead.model_validate(run)
