"""`website_audits`: one stored audit of one business's homepage.

The row keeps what was observed, not what was served. There is no column for the HTML: a
hash proves the page has or has not changed, the visible text is kept only as long as the
retention window allows, and everything a human is shown is a check or a finding carrying
its own evidence snippet.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import created_at_column, uuid_pk

# The version of the rules an audit was produced by. Bumped whenever a check or a finding
# changes meaning, so a stored audit can always be read the way it was written.
#
# audit-2: `tls_valid` is null (not true) on a page served over http; a presence check on
# a parsed page answers false rather than null when the thing is absent; snippet evidence
# comes from the page's visible text instead of a window cut out of its HTML.
# audit-3 (v0.11.0): booking is also recognised from button labels and booking-page link
# paths; new checks `live_chat`, `images_without_alt`, `unlabelled_inputs`, `word_count`
# and `heading_structure`, and the findings built on them plus `builder_subdomain`;
# PageSpeed accessibility and best-practices scores, and their two findings.
RULES_VERSION = "audit-3"


class AuditStatus(enum.StrEnum):
    done = "done"
    # No fetch was needed: the business has no website, or only a social profile.
    skipped = "skipped"
    robots_blocked = "robots_blocked"
    unreachable = "unreachable"
    # Something in our own code or infrastructure went wrong for this one business.
    failed = "failed"


audit_status_enum = SAEnum(
    AuditStatus, name="website_audit_status", values_callable=lambda e: [m.value for m in e]
)


class WebsiteAudit(Base):
    __tablename__ = "website_audits"
    __table_args__ = (
        Index("ix_website_audits_business_id_created_at", "business_id", "created_at"),
        Index("ix_website_audits_job_run_id", "job_run_id"),
        Index("ix_website_audits_status", "status"),
        Index("ix_website_audits_content_expires_at", "content_expires_at"),
        # For `GET /businesses?finding=<code>`: a containment query on the findings array.
        Index("ix_website_audits_findings", "findings", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    # Null for a hand-triggered single audit, which has a run of its own but may also be
    # requested without one.
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    url_audited: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AuditStatus] = mapped_column(audit_status_enum, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)

    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    psi: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Lighthouse's own category scores out of 100 (v0.11.0). Null whenever PSI did not
    # answer — no key, quota, an error — or did not score the category: never a zero.
    accessibility_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_practices_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tech_stack: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    # The visible text, capped. Input for the v0.5.0 AI step and the only part of an audit
    # that expires: `purge-expired` nulls it and leaves everything else in place.
    page_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_version: Mapped[str] = mapped_column(Text, nullable=False, default=RULES_VERSION)
    content_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    @property
    def finding_codes(self) -> list[str]:
        return [str(item.get("code")) for item in (self.findings or []) if item.get("code")]
