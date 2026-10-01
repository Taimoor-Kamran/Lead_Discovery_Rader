"""`domain_intel`: one cached DNS + RDAP snapshot per registrable domain (spec v0.13.0).

Keyed by the domain, not by a business: two businesses on one domain share one lookup.
Each half has its own timestamp and TTL (DNS a week, RDAP a month), and each half is
`null` until it has been answered with certainty — an error is never cached, so it is
asked again next time rather than remembered as a fact. Every audit copies the snapshot
it used into its own `checks.domain_intel`, so this table is a cache, never evidence.

Provenance lives inside each snapshot: `source` (`dns` / `rdap`), the resolver or RDAP URL
asked, and when (decision C17). There is no column for anything about a person.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, updated_at_column


class DomainIntel(Base):
    __tablename__ = "domain_intel"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    dns: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rdap: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    dns_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rdap_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
