"""`match_candidates`: a pair a machine would not decide on its own."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, uuid_pk


class MatchCandidateStatus(enum.StrEnum):
    pending = "pending"
    merged = "merged"
    kept_apart = "kept_apart"


class ResolutionStatus(enum.StrEnum):
    pending = "pending"
    linked = "linked"
    needs_review = "needs_review"
    invalid = "invalid"


match_candidate_status_enum = SAEnum(
    MatchCandidateStatus,
    name="match_candidate_status",
    values_callable=lambda e: [m.value for m in e],
)
resolution_status_enum = SAEnum(
    ResolutionStatus, name="resolution_status", values_callable=lambda e: [m.value for m in e]
)


class MatchCandidate(Base):
    """A record and a business that might be the same thing. Only a human says so."""

    __tablename__ = "match_candidates"
    __table_args__ = (
        UniqueConstraint(
            "discovered_record_id", "business_id", name="uq_match_candidates_record_business"
        ),
        Index("ix_match_candidates_status", "status"),
        Index("ix_match_candidates_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    discovered_record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discovered_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[MatchCandidateStatus] = mapped_column(
        match_candidate_status_enum, nullable=False, default=MatchCandidateStatus.pending
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
