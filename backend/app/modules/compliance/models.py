"""`suppressions`: businesses, domains and phones the system must never propose again.

A row is *active* while `lifted_at` is null. Every place that could turn a business into
work for a human — classification, the review queue, the leads list — checks the active
rows first, by business id, by domain and by phone, so a business rediscovered under a new
id is still caught.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, uuid_pk


class SuppressionSource(enum.StrEnum):
    review = "review"
    admin = "admin"


suppression_source_enum = SAEnum(
    SuppressionSource, name="suppression_source", values_callable=lambda e: [m.value for m in e]
)


class Suppression(Base):
    __tablename__ = "suppressions"
    __table_args__ = (
        Index(
            "ix_suppressions_active_domain",
            "domain",
            postgresql_where=text("lifted_at IS NULL"),
        ),
        Index(
            "ix_suppressions_active_phone_e164",
            "phone_e164",
            postgresql_where=text("lifted_at IS NULL"),
        ),
        Index(
            "ix_suppressions_active_business_id",
            "business_id",
            postgresql_where=text("lifted_at IS NULL"),
        ),
        Index("ix_suppressions_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True
    )
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[SuppressionSource] = mapped_column(suppression_source_enum, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def is_active(self) -> bool:
        return self.lifted_at is None
