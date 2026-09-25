"""Read models for website audits."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.modules.audit_web.models import AuditStatus


class WebsiteAuditSummary(BaseModel):
    """The list view: what was audited, how it went, and which codes came out."""

    id: uuid.UUID
    business_id: uuid.UUID
    job_run_id: uuid.UUID | None
    url_audited: str
    final_url: str | None
    status: AuditStatus
    http_status: int | None
    finding_codes: list[str]
    rules_version: str
    # PageSpeed's category scores out of 100; null whenever PageSpeed did not score them.
    accessibility_score: int | None = None
    best_practices_score: int | None = None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class WebsiteAuditDetail(WebsiteAuditSummary):
    """The whole audit, including the evidence behind every check and finding."""

    checks: dict[str, Any]
    psi: dict[str, Any] | None
    tech_stack: dict[str, Any]
    findings: list[dict[str, Any]]
    # Null for a role that may not read it, and null once retention has purged it.
    # `page_text_hidden` is what tells the two apart.
    page_text: str | None
    page_text_hidden: bool
    html_sha256: str | None
    content_expires_at: datetime | None
    purged_at: datetime | None


class LatestAuditRead(BaseModel):
    """What a business list item says about its newest audit."""

    status: AuditStatus
    finding_codes: list[str]
    audited_at: datetime


class AuditResultSummary(BaseModel):
    """What one `audit` run did, written to `job_runs.result_summary`."""

    audited: int = 0
    skipped: int = 0
    robots_blocked: int = 0
    unreachable: int = 0
    failed: int = 0
    psi_calls: int = 0
