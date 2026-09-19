"""Reading businesses back, with the provenance behind every value."""

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.modules.businesses.models import PROVENANCED_FIELDS, Business, BusinessFieldValue
from app.modules.businesses.schemas import (
    BusinessDetail,
    BusinessSummary,
    FieldValueRead,
    LinkedRecordRead,
)
from app.modules.discovery.models import DiscoveredRecord
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.resolution.survivorship import field_values as normalized_field_values
from app.modules.sources.models import Source


def get_business(session: Session, business_id: uuid.UUID) -> Business:
    business = session.get(Business, business_id)
    if business is None:
        raise NotFoundError("Business not found", details={"business_id": str(business_id)})
    return business


def summarize(business: Business) -> BusinessSummary:
    return BusinessSummary.model_validate(business)


def list_businesses(
    session: Session,
    *,
    industry: str | None = None,
    city: str | None = None,
    state: str | None = None,
    has_website: bool | None = None,
    website_kind: WebsiteKind | None = None,
    business_status: BusinessStatus | None = None,
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[BusinessSummary]:
    """Filtered list, newest first. Every filter is optional and they combine with AND."""
    stmt: Select[tuple[Business]] = (
        select(Business).order_by(Business.created_at.desc(), Business.id.desc()).limit(limit + 1)
    )
    if industry:
        stmt = stmt.where(Business.industry == industry)
    if city:
        stmt = stmt.where(func.lower(Business.city) == city.lower())
    if state:
        stmt = stmt.where(func.upper(Business.state) == state.upper())
    if has_website is True:
        stmt = stmt.where(Business.website.is_not(None))
    if has_website is False:
        stmt = stmt.where(Business.website.is_(None))
    if website_kind is not None:
        stmt = stmt.where(Business.website_kind == website_kind)
    if business_status is not None:
        stmt = stmt.where(Business.business_status == business_status)
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Business.display_name).like(pattern),
                func.lower(Business.normalized_name).like(pattern),
            )
        )

    stmt = apply_cursor(stmt, Business.created_at, Business.id, cursor)
    rows = list(session.scalars(stmt))

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[BusinessSummary](items=[summarize(row) for row in rows], next_cursor=next_cursor)


def detail(session: Session, business: Business) -> BusinessDetail:
    """A business plus every value ever recorded for it and every record behind it."""
    values = list(
        session.scalars(
            select(BusinessFieldValue)
            .where(BusinessFieldValue.business_id == business.id)
            .order_by(BusinessFieldValue.field.asc(), BusinessFieldValue.observed_at.desc())
        )
    )
    source_names = _source_names(session, [value.source_id for value in values])
    displayed = _displayed_values(business)

    records = list(
        session.scalars(
            select(DiscoveredRecord)
            .where(DiscoveredRecord.business_id == business.id)
            .order_by(DiscoveredRecord.first_discovered_at.asc(), DiscoveredRecord.id.asc())
        )
    )
    record_sources = _source_names(session, [record.source_id for record in records])

    return BusinessDetail(
        **summarize(business).model_dump(),
        legal_name=business.legal_name,
        name_key=business.name_key,
        address_line1=business.address_line1,
        address_line2=business.address_line2,
        street_key=business.street_key,
        lat=business.lat,
        lng=business.lng,
        geohash7=business.geohash7,
        places_content_expires_at=business.places_content_expires_at,
        field_values=[
            FieldValueRead(
                field=value.field,
                value=value.value,
                source=source_names.get(value.source_id, ""),
                source_id=value.source_id,
                discovered_record_id=value.discovered_record_id,
                observed_at=value.observed_at,
                expires_at=value.expires_at,
                purged_at=value.purged_at,
                is_displayed=(
                    value.purged_at is None
                    and value.value is not None
                    and displayed.get(value.field) == value.value
                ),
            )
            for value in values
        ],
        records=[
            LinkedRecordRead(
                id=record.id,
                source=record_sources.get(record.source_id, ""),
                source_record_id=record.source_record_id,
                source_url=record.source_url,
                first_discovered_at=record.first_discovered_at,
                last_discovered_at=record.last_discovered_at,
                resolution_status=record.resolution_status.value,
            )
            for record in records
        ],
    )


def _displayed_values(business: Business) -> dict[str, str | None]:
    """What the business currently shows, in the same text form the field values use."""
    shown: dict[str, Any] = {}
    for field in PROVENANCED_FIELDS:
        value = getattr(business, field, None)
        if value is None:
            shown[field] = None
        elif isinstance(value, float):
            shown[field] = repr(value)
        else:
            shown[field] = str(value)
    return shown


def _source_names(session: Session, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not source_ids:
        return {}
    rows = session.execute(
        select(Source.id, Source.name).where(Source.id.in_(set(source_ids)))
    ).all()
    return {row.id: row.name for row in rows}


__all__ = [
    "detail",
    "get_business",
    "list_businesses",
    "normalized_field_values",
    "summarize",
]
