"""`/crm/*`: status, the lead list, retry / send now / sync all, the CSV export, attempts."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.auth.deps import DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.crm import service
from app.modules.crm.models import CrmLeadStatus
from app.modules.crm.schemas import (
    CrmLeadRead,
    CrmStatusRead,
    CrmSyncAttemptRead,
    ExportScope,
    SyncAllResult,
)

crm_router = APIRouter(prefix="/crm", tags=["crm"])

# Blueprint slide 7: the CRM manager owns the export; admins pass everything; a tech admin
# may look at health and history but never sends anything.
CrmManager = Annotated[User, Depends(require_role(Role.crm_manager))]
CrmReader = Annotated[User, Depends(require_role(Role.crm_manager, Role.tech_admin))]


@crm_router.get("/status", response_model=CrmStatusRead)
def crm_status(actor: CrmReader, session: DbSession) -> CrmStatusRead:
    """Destination, `check()` health and counts by status."""
    return service.status(session)


@crm_router.get("/leads", response_model=Page[CrmLeadRead])
def list_crm_leads(
    actor: CrmManager,
    session: DbSession,
    status: Annotated[CrmLeadStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[CrmLeadRead]:
    """Leads at the configured destination, newest first, optionally by status."""
    return service.list_leads(session, status=status, limit=limit, cursor=cursor)


@crm_router.get("/leads/{crm_lead_id}/attempts", response_model=list[CrmSyncAttemptRead])
def crm_lead_attempts(
    crm_lead_id: uuid.UUID, actor: CrmReader, session: DbSession
) -> list[CrmSyncAttemptRead]:
    """Every attempt made on this lead's behalf, newest first."""
    return service.attempts(session, crm_lead_id)


@crm_router.post("/leads/{crm_lead_id}/retry", response_model=CrmLeadRead)
def retry_crm_lead(crm_lead_id: uuid.UUID, actor: CrmManager, session: DbSession) -> CrmLeadRead:
    """Try a held lead again now. The gate is checked again first."""
    return service.retry(session, crm_lead_id, actor=actor)


@crm_router.post("/businesses/{business_id}/sync-now", response_model=CrmLeadRead)
def sync_business_now(business_id: uuid.UUID, actor: CrmManager, session: DbSession) -> CrmLeadRead:
    """Send one business now, skipping the undo-window wait but never the human gate."""
    return service.sync_now(session, business_id, actor=actor)


@crm_router.post("/sync-all", response_model=SyncAllResult)
def sync_all(actor: CrmManager, session: DbSession) -> SyncAllResult:
    """Send every due or held lead. Approvals still inside their undo window wait."""
    return service.sync_all(session, actor=actor)


@crm_router.get("/export.csv", response_class=StreamingResponse)
def export_csv(
    actor: CrmManager,
    session: DbSession,
    scope: Annotated[ExportScope, Query()] = "new",
) -> StreamingResponse:
    """The CSV destination's records: `new` (not yet exported) or `all`. Marks them exported."""
    filename, body = service.export_csv(session, scope=scope, actor=actor)
    return StreamingResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
