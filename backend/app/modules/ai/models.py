"""`ai_classifications`: one row per model call (or per decision not to make one).

Nothing the model said is trusted, and nothing it said is thrown away either: `output` is
what survived the schema and the guardrails, `raw_output` is the text as it came back,
and `rejected_claims` is what was removed in between. Together with the model, the prompt
version, the tokens and the estimated cost, that is the whole provenance of an AI claim.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, uuid_pk


class ClassificationStatus(enum.StrEnum):
    ok = "ok"
    schema_invalid = "schema_invalid"
    guardrail_trimmed = "guardrail_trimmed"
    error = "error"
    skipped_budget = "skipped_budget"
    skipped_disabled = "skipped_disabled"
    reused = "reused"


# The statuses whose `output` may stand in for a new call with the same input.
REUSABLE_STATUSES = frozenset({ClassificationStatus.ok, ClassificationStatus.guardrail_trimmed})

classification_status_enum = SAEnum(
    ClassificationStatus,
    name="ai_classification_status",
    values_callable=lambda e: [m.value for m in e],
)


class AIClassification(Base):
    __tablename__ = "ai_classifications"
    __table_args__ = (
        Index("ix_ai_classifications_input_hash", "input_hash"),
        Index("ix_ai_classifications_created_at", "created_at"),
        Index("ix_ai_classifications_business_id", "business_id"),
        Index("ix_ai_classifications_content_expires_at", "content_expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    website_audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("website_audits.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ClassificationStatus] = mapped_column(classification_status_enum, nullable=False)
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The validated, guardrail-cleaned answer. `none_as_null` so an absent answer is SQL NULL.
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # The model's text as it came back, cut at `AI_RAW_OUTPUT_MAX_CHARS`. Expires with
    # the audit's page text; `purge-expired` nulls it.
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_claims: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    est_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
