"""Read endpoints for what discovery produced."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import CurrentUser, DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.discovery import service
from app.modules.discovery.schemas import DiscoveredRecordDetail, DiscoveredRecordSummary
from app.modules.jobs import service as jobs_service

discovered_records_router = APIRouter(prefix="/discovered-records", tags=["discovery"])
job_records_router = APIRouter(prefix="/jobs", tags=["discovery"])

# The raw payload is source content under a provider's terms, so the detail view is
# limited to the roles that actually work with it.
RecordReader = Annotated[User, Depends(require_role(Role.reviewer, Role.tech_admin))]


@job_records_router.get("/{job_run_id}/records", response_model=Page[DiscoveredRecordSummary])
def list_run_records(
    job_run_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[DiscoveredRecordSummary]:
    jobs_service.get_job_run(session, job_run_id)  # 404 before an empty page
    return service.list_records_for_run(session, job_run_id, limit=limit, cursor=cursor)


@discovered_records_router.get("/{record_id}", response_model=DiscoveredRecordDetail)
def get_discovered_record(
    record_id: uuid.UUID, actor: RecordReader, session: DbSession
) -> DiscoveredRecordDetail:
    return service.detail(session, service.get_record(session, record_id))
