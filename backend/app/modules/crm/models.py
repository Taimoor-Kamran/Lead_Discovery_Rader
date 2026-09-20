"""`crm_leads`, `crm_lead_opportunities`, `crm_sync_attempts` and the dev-only `crm_fake_records`.

A `crm_leads` row is one business at one destination: the CRM record's id when it exists,
where the sync stands, when it is next due and why it last failed. `crm_sync_attempts` is
the log a CRM manager reads on `/crm`: every call made on the lead's behalf, whether it
worked or not.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, updated_at_column, uuid_pk


class CrmLeadStatus(enum.StrEnum):
    scheduled = "scheduled"
    syncing = "syncing"
    synced = "synced"
    held = "held"
    cancelled = "cancelled"
    withdrawn = "withdrawn"


class CrmSyncAction(enum.StrEnum):
    create = "create"
    update = "update"
    link = "link"
    unchanged = "unchanged"
    mark_dnc = "mark_dnc"
    withdraw = "withdraw"
    export = "export"


class CrmSyncStatus(enum.StrEnum):
    ok = "ok"
    failed = "failed"


crm_lead_status_enum = SAEnum(
    CrmLeadStatus, name="crm_lead_status", values_callable=lambda e: [m.value for m in e]
)
crm_sync_action_enum = SAEnum(
    CrmSyncAction, name="crm_sync_action", values_callable=lambda e: [m.value for m in e]
)
crm_sync_status_enum = SAEnum(
    CrmSyncStatus, name="crm_sync_status", values_callable=lambda e: [m.value for m in e]
)


class CrmLead(Base):
    __tablename__ = "crm_leads"
    __table_args__ = (
        UniqueConstraint("business_id", "destination", name="uq_crm_leads_business_destination"),
        Index("ix_crm_leads_status_due_at", "status", "due_at"),
        Index("ix_crm_leads_destination_status", "destination", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CrmLeadStatus] = mapped_column(
        crm_lead_status_enum, nullable=False, default=CrmLeadStatus.scheduled
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    export_batch_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the CRM's Do not contact field last read after a sync, so lifting a suppression
    # knows there is a flag to clear even when the rest of the payload is unchanged.
    do_not_contact_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class CrmLeadOpportunity(Base):
    """Which approved opportunities the CRM record currently carries."""

    __tablename__ = "crm_lead_opportunities"

    crm_lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), primary_key=True
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        primary_key=True,
    )


class CrmSyncAttempt(Base):
    __tablename__ = "crm_sync_attempts"
    __table_args__ = (Index("ix_crm_sync_attempts_crm_lead_id_id", "crm_lead_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    crm_lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[CrmSyncAction] = mapped_column(crm_sync_action_enum, nullable=False)
    status: Mapped[CrmSyncStatus] = mapped_column(crm_sync_status_enum, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = created_at_column()


class FakeCrmRecord(Base):
    """The fake destination's store (development and tests only)."""

    __tablename__ = "crm_fake_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
