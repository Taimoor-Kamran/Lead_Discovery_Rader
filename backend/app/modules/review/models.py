"""`review_decisions` (blueprint slide 41): the full history of what humans decided.

One row per decision on one opportunity. Nothing here is ever updated except to mark a
row undone: `undone_at` / `undone_by` are the only two columns that change after insert,
so the history stays a history.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import uuid_pk
from app.modules.opportunities.models import ReviewStatus, review_status_enum


class Decision(enum.StrEnum):
    approve = "approve"
    reject = "reject"
    needs_enrichment = "needs_enrichment"
    duplicate = "duplicate"
    not_a_fit = "not_a_fit"
    do_not_contact = "do_not_contact"


decision_enum = SAEnum(
    Decision, name="review_decision", values_callable=lambda e: [m.value for m in e]
)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        Index("ix_review_decisions_opportunity_id", "opportunity_id"),
        Index("ix_review_decisions_decided_at_id", "decided_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[Decision] = mapped_column(decision_enum, nullable=False)
    from_status: Mapped[ReviewStatus] = mapped_column(review_status_enum, nullable=False)
    to_status: Mapped[ReviewStatus] = mapped_column(review_status_enum, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
