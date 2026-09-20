"""Building the per-business CRM payload from its approved opportunities.

One record per business, however many services were approved, so two reps never call the
same owner about two things. Everything here is a value a person can read in a CRM cell:
plain labels instead of codes, a formatted phone, ISO dates, text that is never HTML. What
is not known is `None`, never a guess.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.audit_web import service as audits
from app.modules.audit_web.models import WebsiteAudit
from app.modules.auth.models import User
from app.modules.businesses.models import Business
from app.modules.compliance import service as compliance
from app.modules.crm.adapter import CrmRecord
from app.modules.crm.fields import STATUS_NEW, WHY_LEAD_MAX_CHARS, for_create, radar_owned
from app.modules.discovery.models import DiscoveredRecord
from app.modules.opportunities.catalogue import SERVICES
from app.modules.opportunities.models import Opportunity, ReviewStatus
from app.modules.review.models import Decision, ReviewDecision
from app.modules.sources.models import Source

# The short service labels the UI uses (v0.6.0 review pass). The catalogue name is the
# fallback for a service added later, so nothing is ever shown as a raw key.
SERVICE_LABELS: dict[str, str] = {
    "website_design": "Website redesign",
    "seo_gbp": "SEO / Google profile",
    "booking_setup": "Online booking",
    "ads_social": "Ads & social",
}

# Plain-language wording for finding codes, kept in step with the UI's labels.
FINDING_LABELS: dict[str, str] = {
    "no_website": "No website on the listing",
    "social_profile_only": "Social profile instead of a website",
    "unreachable": "Homepage could not be loaded",
    "no_https": "No HTTPS",
    "tls_invalid": "Invalid HTTPS certificate",
    "no_mobile_viewport": "Not mobile-friendly (no viewport tag)",
    "missing_title": "Missing page title",
    "missing_meta_description": "Missing meta description",
    "no_h1": "No main heading",
    "no_structured_data": "No LocalBusiness structured data",
    "no_online_booking": "No online booking",
    "no_contact_on_homepage": "No contact info on homepage",
    "stale_copyright": "Outdated copyright year",
    "slow_mobile": "Slow on mobile",
    "js_shell_suspected": "Page built with JavaScript (audit may be incomplete)",
    "robots_blocked": "Blocked by robots.txt",
}

SOURCE_LABELS: dict[str, str] = {"google_places": "Google Places", "demo_fixture": "Demo fixture"}
SERVICES_SEPARATOR = "; "
_US_PHONE = re.compile(r"^\+1(\d{3})(\d{3})(\d{4})$")


# --- small formatters ---------------------------------------------------------------------


def humanize(code: str | None) -> str | None:
    """`general_contracting` → `General contracting`. Unknown stays `None`."""
    if not code:
        return None
    words = re.sub(r"[_-]+", " ", code).strip()
    return (words[:1].upper() + words[1:]) if words else None


def service_label(key: str) -> str:
    if key in SERVICE_LABELS:
        return SERVICE_LABELS[key]
    spec = SERVICES.get(key)
    return spec.name if spec is not None else (humanize(key) or key)


def finding_label(code: str) -> str:
    return FINDING_LABELS.get(code) or humanize(code) or code


def source_label(name: str) -> str:
    return SOURCE_LABELS.get(name) or humanize(name) or name


def format_phone(e164: str | None) -> str | None:
    """`+15125550102` → `(512) 555-0102`; any other shape is passed through as stored."""
    if not e164:
        return None
    match = _US_PHONE.match(e164.strip())
    if match is None:
        return e164
    return f"({match.group(1)}) {match.group(2)}-{match.group(3)}"


def iso_date(value: datetime | None) -> str | None:
    return value.astimezone(UTC).date().isoformat() if value is not None else None


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


# --- what goes in --------------------------------------------------------------------------


def approved_opportunities(
    session: Session, business_id: uuid.UUID, *, ready_before: datetime | None = None
) -> list[Opportunity]:
    """The business's approved opportunities, strongest first.

    With `ready_before`, only approvals decided at or before that instant: the sync uses it
    to leave an approval alone until its undo window has closed.
    """
    stmt = (
        select(Opportunity)
        .where(
            Opportunity.business_id == business_id,
            Opportunity.review_status == ReviewStatus.approved,
        )
        .order_by(Opportunity.score.desc(), Opportunity.decided_at.asc(), Opportunity.id.asc())
    )
    if ready_before is not None:
        stmt = stmt.where(
            Opportunity.decided_at.is_not(None), Opportunity.decided_at <= ready_before
        )
    return list(session.scalars(stmt))


def build_record(
    session: Session,
    business: Business,
    approved: Sequence[Opportunity],
    *,
    settings: Settings | None = None,
    status: str = STATUS_NEW,
    do_not_contact: bool | None = None,
) -> CrmRecord:
    """The whole record for one business. `approved` may be empty (a withdrawn record)."""
    config = settings or get_settings()
    top = approved[0] if approved else None
    latest = audits.latest_audit(session, business.id)
    source_name, source_url, discovered_at = _origin(session, business, config)
    people = _emails(session, [o.decided_by for o in approved] + [o.assigned_to for o in approved])
    approval_note = _approval_note(session, top) if top is not None else None
    suppressed = (
        compliance.is_suppressed(session, business) if do_not_contact is None else do_not_contact
    )

    address = " ".join(
        part for part in (business.address_line1, business.address_line2) if part and part.strip()
    )
    values: dict[str, Any] = {
        "radar_business_id": str(business.id),
        "business_name": business.display_name,
        "legal_name": business.legal_name,
        "industry": humanize(business.industry),
        "city": business.city,
        "state": business.state,
        "postal_code": business.postal_code,
        "address": address or None,
        "website": business.website,
        "public_phone": format_phone(business.phone_e164),
        "services": SERVICES_SEPARATOR.join(service_label(o.service) for o in approved) or None,
        "lead_score": round(float(top.score) * 100) if top is not None else None,
        "why_lead": _why_lead(approved) or None,
        "top_findings": _top_findings(latest),
        "source": source_name,
        "source_url": source_url,
        "radar_link": f"{config.app_base_url.rstrip('/')}/leads/{top.id}" if top else None,
        "date_discovered": iso_date(discovered_at or business.created_at),
        "date_approved": iso_date(
            min((o.decided_at for o in approved if o.decided_at is not None), default=None)
        ),
        "approved_by": people.get(top.decided_by) if top is not None and top.decided_by else None,
        "do_not_contact": bool(suppressed),
        "assigned_rep": _first_assignee(approved, people),
        "status": status,
        "follow_up_date": None,
        "notes": approval_note,
    }
    return CrmRecord(business_id=business.id, fields=for_create(values))


def payload_hash(record: CrmRecord) -> str:
    """A stable hash of the Radar-owned fields — the part an update would send."""
    canonical = json.dumps(
        radar_owned(record.fields), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- pieces -------------------------------------------------------------------------------


def _why_lead(approved: Sequence[Opportunity]) -> str:
    """The rules' wording per approved service; the model's rationale is not a reason."""
    from app.modules.review.service import split_reason

    lines: list[str] = []
    for row in approved:
        rule_reason, _ = split_reason(row, _ai_output(row))
        text = (rule_reason or "").strip()
        if text:
            lines.append(f"{service_label(row.service)}: {text}")
    return clip("\n".join(lines), WHY_LEAD_MAX_CHARS)


def _ai_output(row: Opportunity) -> dict[str, Any] | None:
    if row.ai_classification_id is None:
        return None
    from sqlalchemy.orm import object_session

    from app.modules.ai.models import AIClassification

    session = object_session(row)
    if session is None:
        return None
    classification = session.get(AIClassification, row.ai_classification_id)
    return classification.output if classification is not None else None


def _top_findings(latest: WebsiteAudit | None) -> str | None:
    if latest is None:
        return None
    from app.modules.review.service import top_findings

    labels = [finding_label(code) for code in top_findings(latest)]
    return SERVICES_SEPARATOR.join(labels) or None


def _origin(
    session: Session, business: Business, settings: Settings
) -> tuple[str | None, str | None, datetime | None]:
    """Which source found the business (highest priority first) and its listing URL."""
    rows = session.execute(
        select(Source.name, DiscoveredRecord.source_url, DiscoveredRecord.first_discovered_at)
        .join(Source, Source.id == DiscoveredRecord.source_id)
        .where(DiscoveredRecord.business_id == business.id)
        .order_by(DiscoveredRecord.first_discovered_at.asc())
    ).all()
    if not rows:
        return None, None, None
    priority = {name: index for index, name in enumerate(settings.resolution_source_priority)}
    best = min(rows, key=lambda row: (priority.get(row[0], len(priority)), row[2]))
    earliest = min(row[2] for row in rows)
    return source_label(best[0]), best[1], earliest


def _emails(session: Session, ids: Sequence[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {item for item in ids if item is not None}
    if not wanted:
        return {}
    rows = session.execute(select(User.id, User.email).where(User.id.in_(wanted))).all()
    return {row.id: row.email for row in rows}


def _first_assignee(approved: Sequence[Opportunity], people: dict[uuid.UUID, str]) -> str | None:
    for row in approved:
        if row.assigned_to is not None and row.assigned_to in people:
            return people[row.assigned_to]
    return None


def _approval_note(session: Session, top: Opportunity) -> str | None:
    """The reviewer's note on the approval that is in force, if they wrote one."""
    row = session.scalars(
        select(ReviewDecision)
        .where(
            ReviewDecision.opportunity_id == top.id,
            ReviewDecision.decision == Decision.approve,
            ReviewDecision.undone_at.is_(None),
        )
        .order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id.desc())
        .limit(1)
    ).first()
    return (row.note or "").strip() or None if row is not None else None
