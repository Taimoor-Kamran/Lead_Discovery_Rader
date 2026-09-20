"""`alerts`: what the health page shows in its banner (blueprint slide 45).

One row per rule while its condition holds. A row is *active* until `cleared_at` is set:
a condition alert (success rate, error rate, held leads, budget, backup age, queue) is
cleared by the evaluator once the condition no longer holds; an event alert (a stale run,
a failed backup verify) is cleared when a human acknowledges it. Acknowledging hides a
row from the banner but keeps it until it clears, so a persisting condition does not
nag and does not re-fire until it has actually gone away and come back.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, uuid_pk


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_rule_cleared_at", "rule", "cleared_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    @property
    def active(self) -> bool:
        return self.cleared_at is None

    @property
    def acknowledged(self) -> bool:
        return self.acknowledged_at is not None
