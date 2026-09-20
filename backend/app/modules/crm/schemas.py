"""Read models for `/crm` and the `crm` block on the leads endpoints."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.modules.crm.models import CrmLeadStatus, CrmSyncAction, CrmSyncStatus


class CrmCheckRead(BaseModel):
    name: str
    ok: bool
    detail: str


class CrmHealthRead(BaseModel):
    destination: str
    ok: bool
    checks: list[CrmCheckRead]
    message: str | None


class CrmStatusRead(BaseModel):
    """`GET /crm/status`: where leads go, whether it works, and how many stand where."""

    destination: str
    demo: bool
    auto_sync: bool
    sync_delay_minutes: int
    health: CrmHealthRead
    counts: dict[str, int]


class CrmSyncAttemptRead(BaseModel):
    id: int
    crm_lead_id: uuid.UUID
    action: CrmSyncAction
    status: CrmSyncStatus
    http_status: int | None
    error: str | None
    duration_ms: int
    created_at: datetime


class CrmLeadRead(BaseModel):
    """One business at the configured destination, as `/crm` lists it."""

    id: uuid.UUID
    business_id: uuid.UUID
    business_name: str
    city: str | None
    state: str | None
    destination: str
    status: CrmLeadStatus
    external_id: str | None
    external_url: str | None
    due_at: datetime | None
    attempts: int
    last_error: str | None
    last_synced_at: datetime | None
    export_batch_id: str | None
    services: list[str]
    created_at: datetime
    updated_at: datetime


class CrmLeadStatusRead(BaseModel):
    """The `crm` block on a lead: enough for a badge, a link and a Retry button."""

    id: uuid.UUID
    status: CrmLeadStatus
    external_url: str | None
    last_synced_at: datetime | None
    due_at: datetime | None
    last_error: str | None


class SyncAllResult(BaseModel):
    considered: int
    synced: int
    held: int
    scheduled: int
    cancelled: int


ExportScope = Literal["new", "all"]
