"""Where a business's details came from, in the shape a screen can read out loud.

Nothing here discovers, fetches or stores anything. Every value is already in the database:
`discovered_records` carries one row per record a source gave us, with its `source_url`,
`first_discovered_at` and `last_discovered_at`, and the latest website audit carries the
`social_links` check the v0.4.0 audit recorded on the business's own homepage.

Two rules hold throughout:

* **One entry per contributing record**, not per survivorship winner. A business built from
  two discovered records is answering for both of them.
* **A linked profile is a link the business published, never a source we read.** The audit
  records which platforms the homepage points at; no profile is ever fetched. The wording
  on the page has to say that, so this module hands back the page the links were read on
  alongside them.
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.modules.audit_web.checks import EVIDENCE_MAX_CHARS
from app.modules.audit_web.models import WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord
from app.modules.review.schemas import LinkedProfileRead, LinkedProfilesRead, SourceRecordRead
from app.modules.sources.models import Source

# The audit check that records which platforms the homepage links to.
SOCIAL_LINKS_CHECK = "social_links"
# Only these may become an href. Anything else is named without a link rather than guessed.
LINKABLE_SCHEMES = ("http://", "https://")


def source_records(session: Session, business_id: uuid.UUID) -> list[SourceRecordRead]:
    """Every discovered record this business was built from, earliest first."""
    rows = list(
        session.execute(
            select(DiscoveredRecord, Source.name, Source.config)
            .join(Source, Source.id == DiscoveredRecord.source_id)
            .where(DiscoveredRecord.business_id == business_id)
            .order_by(
                DiscoveredRecord.first_discovered_at.asc(),
                DiscoveredRecord.id.asc(),
            )
        )
    )
    return [
        SourceRecordRead(
            code=name,
            name=_display_name(name, config),
            source_record_id=record.source_record_id,
            source_url=record.source_url,
            discovered_at=record.first_discovered_at,
            last_seen_at=record.last_discovered_at,
        )
        for record, name, config in rows
    ]


def source_codes(session: Session, business_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """The distinct source codes behind each business, for the list views' source column."""
    if not business_ids:
        return {}
    grouped: dict[uuid.UUID, list[str]] = defaultdict(list)
    rows = session.execute(
        select(DiscoveredRecord.business_id, Source.name)
        .join(Source, Source.id == DiscoveredRecord.source_id)
        .where(DiscoveredRecord.business_id.in_(business_ids))
        .group_by(DiscoveredRecord.business_id, Source.name)
        .order_by(Source.name.asc())
    ).all()
    for business_id, name in rows:
        grouped[business_id].append(name)
    return dict(grouped)


def linked_profiles(audit: WebsiteAudit | None) -> LinkedProfilesRead:
    """The social profiles the business's own homepage links to, as the audit recorded them.

    The check stores the platform keys as its value and `"key: href; key: href"` as its
    evidence, clipped to `EVIDENCE_MAX_CHARS`. The keys are authoritative; an href is only
    offered when it parsed cleanly out of evidence that was not clipped mid-URL. A platform
    whose link cannot be recovered is still named — with `url = null`, never a guess.
    """
    if audit is None:
        return LinkedProfilesRead(page_url=None, profiles=[])
    check = (audit.checks or {}).get(SOCIAL_LINKS_CHECK)
    if not isinstance(check, dict):
        return LinkedProfilesRead(page_url=None, profiles=[])
    platforms = [str(key) for key in check.get("value") or [] if str(key)]
    if not platforms:
        return LinkedProfilesRead(page_url=None, profiles=[])
    hrefs = _hrefs(check.get("evidence_text"))
    return LinkedProfilesRead(
        page_url=_linkable(check.get("evidence_url")),
        profiles=[
            LinkedProfileRead(platform=platform, url=hrefs.get(platform)) for platform in platforms
        ],
    )


def _hrefs(evidence_text: Any) -> dict[str, str]:
    """`"facebook: https://…; instagram: https://…"` taken apart, conservatively.

    Evidence is clipped, so the last pair may be a truncated URL that would render as a
    working link to the wrong place. When the text is at the clip limit the last pair is
    dropped: the platform is still named from the check's value, just without a link.
    """
    if not isinstance(evidence_text, str) or not evidence_text:
        return {}
    pairs = evidence_text.split("; ")
    if len(evidence_text) >= EVIDENCE_MAX_CHARS and pairs:
        pairs = pairs[:-1]
    found: dict[str, str] = {}
    for pair in pairs:
        key, separator, href = pair.partition(": ")
        if not separator:
            continue
        linkable = _linkable(href.strip())
        if linkable is not None:
            found.setdefault(key.strip(), linkable)
    return found


def _linkable(value: Any) -> str | None:
    """A URL only when it is one we would let the browser open; otherwise nothing."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if candidate.lower().startswith(LINKABLE_SCHEMES) else None


def _display_name(name: str, config: dict[str, Any] | None) -> str:
    """The operator-facing name on the `sources` row. The screen prefers its own label."""
    display = (config or {}).get("display_name")
    return str(display) if isinstance(display, str) and display.strip() else name


# --- the recency filter ---------------------------------------------------------------------


def discovered_within(
    stmt: Select[Any], days: int | None, *, now: datetime | None = None
) -> Select[Any]:
    """Narrow a statement selecting `Business` to businesses **first found** within `days`.

    "First found", not "last seen": the window is measured against the earliest
    `discovered_records.first_discovered_at` of the business, so a record that was seen
    again this morning does not make a business discovered last year look new. A business
    with no discovered record at all was never found within any window, so the join drops it.
    """
    if days is None:
        return stmt
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    earliest = (
        select(
            DiscoveredRecord.business_id.label("business_id"),
            func.min(DiscoveredRecord.first_discovered_at).label("first_discovered_at"),
        )
        .where(DiscoveredRecord.business_id.is_not(None))
        .group_by(DiscoveredRecord.business_id)
        .subquery()
    )
    return stmt.join(earliest, earliest.c.business_id == Business.id).where(
        earliest.c.first_discovered_at >= cutoff
    )


__all__ = [
    "discovered_within",
    "linked_profiles",
    "source_codes",
    "source_records",
]
