"""Which value a business shows, and where it came from.

A business row holds no facts of its own: each displayed value is the winner among the
`business_field_values` written for it. Recomputing is therefore always safe, and it is
what makes both a merge and a retention purge show up correctly.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.businesses.models import PROVENANCED_FIELDS, Business, BusinessFieldValue
from app.modules.discovery.models import DiscoveredRecord
from app.modules.normalization.schemas import BusinessStatus, NormalizedBusiness, WebsiteKind
from app.modules.sources.models import Source

logger = get_logger("app.resolution.survivorship")

# Shown when every value for the name has been purged. The place ID may be kept
# indefinitely, so the business stays identifiable and re-discovery refreshes it.
EXPIRED_NAME_TEMPLATE = "[expired] {source_record_id}"

# Fields that only mean anything together. Chosen one by one, a merged business could show
# one record's Facebook page beside another record's own domain — a web presence no source
# ever reported, and the same for half of one address beside half of another. Each group is
# therefore taken whole, from a single record.
WEBSITE_GROUP = ("website", "domain", "website_kind")
# `website_kind` only describes the other two, so it is not what makes a record a candidate:
# a record that knows of no site cannot win the group away from one that does.
WEBSITE_GROUP_IDENTITY = ("website", "domain")
ADDRESS_GROUP = (
    "address_line1",
    "address_line2",
    "street_key",
    "city",
    "state",
    "postal_code",
    "country",
    "lat",
    "lng",
    "geohash7",
)
GROUPED_FIELDS = frozenset(WEBSITE_GROUP + ADDRESS_GROUP)

# Which record answers "where is this business online?" best: a real site beats a site
# builder, which beats a social page. `none` can only win when nothing else is known.
WEBSITE_KIND_RANK = {
    WebsiteKind.own_site.value: 0,
    WebsiteKind.builder_subdomain.value: 1,
    WebsiteKind.social_profile.value: 2,
    WebsiteKind.none.value: 3,
}

# The surviving rows of one discovered record, keyed by field.
RecordFields = dict[str, BusinessFieldValue]
SortKey = Callable[[BusinessFieldValue], tuple[int, float, int]]


def field_values(normalized: NormalizedBusiness) -> dict[str, str | None]:
    """One normalized record flattened to `{field: text}`. `None` stays `None`."""
    return {
        "display_name": normalized.display_name,
        "normalized_name": normalized.normalized_name,
        "name_key": normalized.name_key,
        "legal_name": normalized.legal_name,
        "industry": normalized.industry,
        "address_line1": normalized.address.line1,
        "address_line2": normalized.address.line2,
        "street_key": normalized.address.street_key,
        "city": normalized.address.city,
        "state": normalized.address.state,
        "postal_code": normalized.address.postal_code,
        "country": normalized.address.country,
        "lat": None if normalized.lat is None else repr(normalized.lat),
        "lng": None if normalized.lng is None else repr(normalized.lng),
        "geohash7": normalized.geohash7,
        "phone_e164": normalized.phone_e164,
        "website": normalized.website,
        "domain": normalized.domain,
        "website_kind": normalized.website_kind.value,
        "business_status": normalized.business_status.value,
    }


def write_field_values(
    session: Session,
    *,
    business: Business,
    record: DiscoveredRecord,
    normalized: NormalizedBusiness,
    observed_at: datetime,
) -> list[BusinessFieldValue]:
    """Record what this source said about this business, one row per field.

    Re-running for the same record updates its rows rather than adding a second set, so
    resolution is idempotent and the provenance trail does not grow on every pass.
    """
    existing = {
        row.field: row
        for row in session.scalars(
            select(BusinessFieldValue).where(
                BusinessFieldValue.business_id == business.id,
                BusinessFieldValue.discovered_record_id == record.id,
            )
        )
    }
    written: list[BusinessFieldValue] = []
    for field, value in field_values(normalized).items():
        row = existing.get(field)
        if row is None:
            row = BusinessFieldValue(
                business_id=business.id,
                field=field,
                source_id=record.source_id,
                discovered_record_id=record.id,
            )
            session.add(row)
        row.value = value
        row.observed_at = observed_at
        row.expires_at = record.content_expires_at
        row.purged_at = None
        written.append(row)
    session.flush()
    return written


def recompute(session: Session, business: Business, *, now: datetime | None = None) -> Business:
    """Rebuild every displayed value on the business from its field values."""
    priority = _priority_index(session)
    rows = list(
        session.scalars(
            select(BusinessFieldValue).where(
                BusinessFieldValue.business_id == business.id,
                BusinessFieldValue.purged_at.is_(None),
            )
        )
    )
    source_names = _source_names(session, [row.source_id for row in rows])

    def sort_key(row: BusinessFieldValue) -> tuple[int, float, int]:
        """Source priority first, then the most recent, then the newest row."""
        return (
            priority.get(source_names.get(row.source_id, ""), len(priority)),
            -row.observed_at.timestamp(),
            -row.id,
        )

    by_record: dict[uuid.UUID, RecordFields] = {}
    for row in rows:
        by_record.setdefault(row.discovered_record_id, {})[row.field] = row

    resolved: dict[str, str | None] = {}
    website = _group_winner(
        by_record,
        group=WEBSITE_GROUP,
        identity=WEBSITE_GROUP_IDENTITY,
        rank=_website_rank,
        sort_key=sort_key,
    )
    address = _group_winner(
        by_record,
        group=ADDRESS_GROUP,
        identity=ADDRESS_GROUP,
        rank=_address_rank,
        sort_key=sort_key,
    )
    for group, winner in ((WEBSITE_GROUP, website), (ADDRESS_GROUP, address)):
        for field in group:
            won = winner.get(field)
            resolved[field] = won.value if won is not None else None

    for row in sorted(rows, key=sort_key):
        if row.field in GROUPED_FIELDS or row.value is None:
            continue
        resolved.setdefault(row.field, row.value)

    for field in PROVENANCED_FIELDS:
        _apply(business, field, resolved.get(field))

    if not resolved.get("display_name"):
        business.display_name = _expired_name(session, business)

    expiries = [row.expires_at for row in rows if row.expires_at is not None]
    business.places_content_expires_at = min(expiries) if expiries else None
    session.flush()
    return business


def _group_winner(
    by_record: dict[uuid.UUID, RecordFields],
    *,
    group: tuple[str, ...],
    identity: tuple[str, ...],
    rank: Callable[[RecordFields], int],
    sort_key: SortKey,
) -> RecordFields:
    """The one record the whole group is read from, or `{}` when no record knows any of it.

    A record that knows nothing about the group cannot win it, so a group never falls back
    field by field: what is shown is what one source actually said, together.
    """
    best: RecordFields = {}
    best_key: tuple[int, tuple[int, float, int]] | None = None
    for fields in by_record.values():
        rows = [fields[field] for field in group if field in fields]
        if not rows:
            continue
        if all(fields[field].value is None for field in identity if field in fields):
            continue
        key = (rank(fields), min(sort_key(row) for row in rows))
        if best_key is None or key < best_key:
            best, best_key = fields, key
    return best


def _website_rank(fields: RecordFields) -> int:
    """Prefer a real site, then a site builder, then a social page, then nothing."""
    row = fields.get("website_kind")
    value = row.value if row is not None else None
    return WEBSITE_KIND_RANK.get(value or WebsiteKind.none.value, len(WEBSITE_KIND_RANK))


def _address_rank(fields: RecordFields) -> int:
    """Prefer an address that reaches a street over one that only knows the town."""
    for field in ("street_key", "address_line1"):
        row = fields.get(field)
        if row is not None and row.value is not None:
            return 0
    return 1


def _apply(business: Business, field: str, value: str | None) -> None:
    """Write one field back onto the business, converting text to its column type."""
    if field in {"lat", "lng"}:
        setattr(business, field, _as_float(value))
    elif field == "website_kind":
        business.website_kind = _as_enum(WebsiteKind, value, WebsiteKind.none)
    elif field == "business_status":
        business.business_status = _as_enum(BusinessStatus, value, BusinessStatus.unknown)
    elif field == "display_name":
        if value is not None:
            business.display_name = value
    else:
        setattr(business, field, value)


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        logger.warning("a stored coordinate is not a number", extra={"value": value})
        return None


def _as_enum(enum_type: Any, value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return enum_type(value)
    except ValueError:
        return fallback


def _expired_name(session: Session, business: Business) -> str:
    """The identity that survives a purge: the source's own record id."""
    record_id = session.scalar(
        select(DiscoveredRecord.source_record_id)
        .where(DiscoveredRecord.business_id == business.id)
        .order_by(DiscoveredRecord.first_discovered_at.asc(), DiscoveredRecord.id.asc())
        .limit(1)
    )
    return EXPIRED_NAME_TEMPLATE.format(source_record_id=record_id or business.id)


def _priority_index(session: Session) -> dict[str, int]:
    """Source name to rank. Sources not on the list come last, in name order."""
    configured = get_settings().resolution_source_priority
    index = {name: position for position, name in enumerate(configured)}
    for name in sorted(session.scalars(select(Source.name))):
        index.setdefault(name, len(index))
    return index


def _source_names(session: Session, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not source_ids:
        return {}
    rows = session.execute(
        select(Source.id, Source.name).where(Source.id.in_(set(source_ids)))
    ).all()
    return {row.id: row.name for row in rows}
