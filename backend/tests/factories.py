"""In-memory businesses and audits for the unit tests. Nothing here touches a database."""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.modules.audit_web import findings as findings_module
from app.modules.audit_web.models import RULES_VERSION, AuditStatus, WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
PAGE_URL = "https://example-plumbing.invalid/"


def make_business(**overrides: Any) -> Business:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "display_name": "Example Plumbing",
        "industry": "plumbing",
        "city": "Austin",
        "state": "TX",
        "phone_e164": "+15125550100",
        "address_line1": "100 Congress Ave",
        "postal_code": "78701",
        "website": PAGE_URL,
        "domain": "example-plumbing.invalid",
        "website_kind": WebsiteKind.own_site,
        "business_status": BusinessStatus.operational,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Business(**values)


def finding(code: str, *, text: str | None = None, url: str | None = PAGE_URL) -> dict[str, Any]:
    """A real catalogue finding, so the message is the one the audit would have written."""
    wording: dict[str, Any] = {}
    if code in ("unreachable", "tls_invalid"):
        wording["url"] = url
    if code == "stale_copyright":
        wording["year"] = 2016
    if code == "slow_mobile":
        wording["score"] = 41
    if code == "images_without_alt":
        wording.update(missing=37, total=41)
    if code == "unlabelled_form_fields":
        wording.update(count=4, noun="form fields")
    if code == "thin_content":
        wording.update(words=84, noun="words")
    if code == "heading_level_skipped":
        wording.update(higher="h1", lower="h3")
    return findings_module.build(
        code, evidence_text=text or f"evidence for {code}", evidence_url=url, **wording
    ).as_dict()


def check(value: Any, text: str | None = None, url: str | None = PAGE_URL) -> dict[str, Any]:
    return {"value": value, "evidence_text": text, "evidence_url": url}


def make_audit(
    business: Business,
    *,
    status: AuditStatus = AuditStatus.done,
    findings: list[dict[str, Any]] | None = None,
    checks: dict[str, Any] | None = None,
    page_text: str | None = "Family-run plumbing. We fix leaks and clear drains.",
    psi: dict[str, Any] | None = None,
    tech_stack: dict[str, Any] | None = None,
    social_links: list[str] | None = None,
) -> WebsiteAudit:
    resolved_checks = {"parsed": check(True), "social_links": check(social_links or [])}
    if status is not AuditStatus.done:
        resolved_checks = {}
    resolved_checks.update(checks or {})
    return WebsiteAudit(
        id=uuid.uuid4(),
        business_id=business.id,
        url_audited=business.website or "",
        final_url=PAGE_URL if status is AuditStatus.done else None,
        status=status,
        checks=resolved_checks,
        psi=psi,
        tech_stack=tech_stack or {"platforms": []},
        findings=findings or [],
        page_text=page_text if status is AuditStatus.done else None,
        rules_version=RULES_VERSION,
        created_at=NOW,
    )
