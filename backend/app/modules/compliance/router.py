"""`/suppressions`: the do-not-contact list, for admins."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.compliance import service
from app.modules.compliance.schemas import SuppressionCreate, SuppressionRead

suppressions_router = APIRouter(prefix="/suppressions", tags=["compliance"])

# Reading the list helps anyone who reviews or exports; changing it is an admin's job.
SuppressionReader = Annotated[
    User, Depends(require_role(Role.reviewer, Role.crm_manager, Role.tech_admin))
]
SuppressionAdmin = Annotated[User, Depends(require_role(Role.admin))]


@suppressions_router.get("", response_model=Page[SuppressionRead])
def list_suppressions(
    actor: SuppressionReader,
    session: DbSession,
    active_only: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[SuppressionRead]:
    return service.list_suppressions(session, active_only=active_only, limit=limit, cursor=cursor)


@suppressions_router.post("", response_model=SuppressionRead, status_code=status.HTTP_201_CREATED)
def add_suppression(
    payload: SuppressionCreate, actor: SuppressionAdmin, session: DbSession
) -> SuppressionRead:
    """Add a suppression by business, domain and/or phone. Effective immediately."""
    row = service.add_suppression(session, payload, actor_id=actor.id)
    return service.read(row)


@suppressions_router.post("/{suppression_id}/lift", response_model=SuppressionRead)
def lift_suppression(
    suppression_id: uuid.UUID, actor: SuppressionAdmin, session: DbSession
) -> SuppressionRead:
    row = service.lift_suppression(session, suppression_id, actor_id=actor.id)
    return service.read(row)
