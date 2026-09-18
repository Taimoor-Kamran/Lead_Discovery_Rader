"""Which value a business shows, and where it came from.

A business row holds no facts of its own: each displayed value is the winner among the
`business_field_values` written for it. Recomputing is therefore always safe, and it is
what makes both a merge and a retention purge show up correctly.
"""

import uuid
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

    winners: dict[str, BusinessFieldValue] = {}
    for row in sorted(
        rows,
        key=lambda r: (
            priority.get(source_names.get(r.source_id, ""), len(priority)),
            -r.observed_at.timestamp(),
            -r.id,
        ),
    ):
        if row.value is not None and row.field not in winners:
            winners[row.field] = row

    for field in PROVENANCED_FIELDS:
        winner = winners.get(field)
        _apply(business, field, winner.value if winner else None)

    if not winners.get("display_name"):
        business.display_name = _expired_name(session, business)

    expiries = [row.expires_at for row in rows if row.expires_at is not None]
    business.places_content_expires_at = min(expiries) if expiries else None
    session.flush()
    return business


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
