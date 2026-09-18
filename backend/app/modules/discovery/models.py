"""What discovery stores: the raw record, who saw it when, and what each call cost.

`discovered_records` is deliberately raw. Nothing here is cleaned, deduplicated against a
business or scored — that is v0.3.0 onwards. What every row does carry is full provenance,
so any downstream fact can be traced back to the exact response it came from.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, updated_at_column, uuid_pk


class DiscoveredRecord(Base):
    """One record from one source, upserted every time it is seen again."""

    __tablename__ = "discovered_records"
    __table_args__ = (
        UniqueConstraint("source_id", "source_record_id", name="uq_discovered_records_source_key"),
        Index("ix_discovered_records_content_expires_at", "content_expires_at"),
        Index("ix_discovered_records_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nulled by `purge-expired` once the provider's storage window closes; the row and its
    # source_record_id survive, so the record can be re-fetched rather than re-discovered.
    # `none_as_null` matters: without it SQLAlchemy would store the JSON scalar `null`,
    # which reads back as None but is not SQL NULL, so `purged is null` would never match.
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The FK to `businesses` arrives with entity resolution in v0.3.0.
    business_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    content_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class RecordSighting(Base):
    """Which run saw which record, and where it ranked in that run's results."""

    __tablename__ = "record_sightings"
    __table_args__ = (
        UniqueConstraint(
            "discovered_record_id", "job_run_id", name="uq_record_sightings_record_run"
        ),
        Index("ix_record_sightings_job_run_id", "job_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    discovered_record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discovered_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_runs.id", ondelete="CASCADE"), nullable=False
    )
    search_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("search_jobs.id", ondelete="CASCADE"), nullable=True
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ApiCall(Base):
    """One outbound attempt to a source. Written whether the attempt worked or not."""

    __tablename__ = "api_calls"
    __table_args__ = (Index("ix_api_calls_source_id_created_at", "source_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_class: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
