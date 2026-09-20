"""`opportunities` (blueprint slide 28): a business is a fact, an opportunity is a claim.

One row says "this service fits this business", why, on what evidence, with what
confidence and score, and what a human decided about it (v0.6.0). The partial unique
index guarantees a business has at most one pending opportunity per service:
re-classification updates it in place.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, updated_at_column, uuid_pk


class OpportunitySource(enum.StrEnum):
    rules = "rules"
    ai = "ai"
    rules_and_ai = "rules+ai"


class ReviewStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    needs_enrichment = "needs_enrichment"
    duplicate = "duplicate"
    not_a_fit = "not_a_fit"
    do_not_contact = "do_not_contact"


opportunity_source_enum = SAEnum(
    OpportunitySource, name="opportunity_source", values_callable=lambda e: [m.value for m in e]
)
review_status_enum = SAEnum(
    ReviewStatus, name="review_status", values_callable=lambda e: [m.value for m in e]
)


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        # One *pending* opportunity per business and service. Decided ones may pile up
        # over time; the open one is always unique.
        Index(
            "uq_opportunities_pending_business_service",
            "business_id",
            "service",
            unique=True,
            postgresql_where=text("review_status = 'pending'"),
        ),
        Index("ix_opportunities_review_status_score", "review_status", "score"),
        Index("ix_opportunities_business_id", "business_id"),
        Index("ix_opportunities_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    website_audit_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("website_audits.id", ondelete="SET NULL"), nullable=True
    )
    ai_classification_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_classifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    service: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[OpportunitySource] = mapped_column(opportunity_source_enum, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    # Null when the AI did not look; true/false when it did and did/did not name this service.
    ai_agrees: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scoring_version: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[ReviewStatus] = mapped_column(
        review_status_enum, nullable=False, default=ReviewStatus.pending
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # v0.6.0: who decided, when, and the optimistic-lock counter every decision request
    # must echo back. A stale `lock_version` is a 409: someone else decided first.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
