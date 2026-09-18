"""Search jobs (what to look for) and job runs (one execution of some work)."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, updated_at_column, uuid_pk


class SearchJobStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    archived = "archived"


class JobRunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({JobRunStatus.done, JobRunStatus.failed, JobRunStatus.cancelled})

search_job_status_enum = SAEnum(
    SearchJobStatus, name="search_job_status", values_callable=lambda e: [m.value for m in e]
)
job_run_status_enum = SAEnum(
    JobRunStatus, name="job_run_status", values_callable=lambda e: [m.value for m in e]
)


class SearchJob(Base):
    __tablename__ = "search_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    geo: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    industry: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list
    )
    status: Mapped[SearchJobStatus] = mapped_column(
        search_job_status_enum, nullable=False, default=SearchJobStatus.draft
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    search_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("search_jobs.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobRunStatus] = mapped_column(
        job_run_status_enum, nullable=False, default=JobRunStatus.queued, index=True
    )
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    # What the run was asked to do, e.g. {"parent_run_id": ...} for a resolution run.
    # Set when the run is enqueued and never written by the handler.
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # What the handler produced, e.g. {"fetched", "stored_new", "updated", "invalid", "api_calls"}.
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES
