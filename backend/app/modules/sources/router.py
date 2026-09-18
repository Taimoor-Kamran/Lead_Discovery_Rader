"""`/sources` endpoints: list for anyone signed in, toggle for admins."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.auth.deps import CurrentUser, DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.sources import service
from app.modules.sources.schemas import SourceRead, SourceUpdate

sources_router = APIRouter(prefix="/sources", tags=["sources"])

SourceAdmin = Annotated[User, Depends(require_role(Role.tech_admin))]


@sources_router.get("", response_model=list[SourceRead])
def list_sources(user: CurrentUser, session: DbSession) -> list[SourceRead]:
    return service.list_sources(session)


@sources_router.patch("/{source_id}", response_model=SourceRead)
def update_source(
    source_id: uuid.UUID, payload: SourceUpdate, actor: SourceAdmin, session: DbSession
) -> SourceRead:
    source = service.set_enabled(session, source_id, enabled=payload.enabled, actor_id=actor.id)
    return SourceRead.model_validate(source)
