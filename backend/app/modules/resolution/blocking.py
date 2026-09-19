"""Which existing businesses are worth comparing a record against.

Scoring every record against every business is quadratic and pointless. Blocking narrows
the field to the businesses that share a strong key or sit in the same map cell, and caps
how many of those one record may be scored against.
"""

from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.businesses.models import Business
from app.modules.normalization.geo import neighbours
from app.modules.normalization.schemas import NormalizedBusiness, WebsiteKind

# How many rows one geohash block may pull back before the name filter thins it out.
GEO_FETCH_LIMIT = 200


@dataclass(frozen=True)
class BlockingKeys:
    """The keys a record is looked up by. Each is `None` when the record cannot supply it."""

    domain: str | None = None
    phone_e164: str | None = None
    postal_name: tuple[str, str] | None = None
    geohash_cells: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.domain or self.phone_e164 or self.postal_name or self.geohash_cells)


def candidate_keys(normalized: NormalizedBusiness) -> BlockingKeys:
    """A social profile is never a key: half a city shares one platform, not one business."""
    domain = (
        normalized.domain
        if normalized.domain and normalized.website_kind is not WebsiteKind.social_profile
        else None
    )
    postal_name = (
        (normalized.address.postal_code, normalized.name_key)
        if normalized.address.postal_code and normalized.name_key
        else None
    )
    cells: tuple[str, ...] = ()
    if normalized.lat is not None and normalized.lng is not None:
        cells = tuple(neighbours(normalized.lat, normalized.lng))

    return BlockingKeys(
        domain=domain,
        phone_e164=normalized.phone_e164,
        postal_name=postal_name,
        geohash_cells=cells,
    )


def find_candidates(
    session: Session,
    normalized: NormalizedBusiness,
    *,
    limit: int | None = None,
    exclude: set[object] | None = None,
) -> list[Business]:
    """Candidate businesses, strongest block first, capped at `RESOLUTION_MAX_CANDIDATES`.

    The order matters: when more candidates exist than the cap allows, the ones that share
    a domain or a phone are the ones kept.
    """
    settings = get_settings()
    cap = limit if limit is not None else settings.resolution_max_candidates
    keys = candidate_keys(normalized)
    if keys.is_empty or cap <= 0:
        return []

    skip = exclude or set()
    found: dict[object, Business] = {}

    def collect(rows: list[Business]) -> None:
        for row in rows:
            if row.id not in skip and row.id not in found and len(found) < cap:
                found[row.id] = row

    if keys.domain:
        collect(list(session.scalars(select(Business).where(Business.domain == keys.domain))))
    if keys.phone_e164 and len(found) < cap:
        collect(
            list(session.scalars(select(Business).where(Business.phone_e164 == keys.phone_e164)))
        )
    if keys.postal_name and len(found) < cap:
        postal, name_key = keys.postal_name
        collect(
            list(
                session.scalars(
                    select(Business).where(
                        Business.postal_code == postal, Business.name_key == name_key
                    )
                )
            )
        )
    if keys.geohash_cells and len(found) < cap:
        collect(_nearby_by_name(session, normalized, keys.geohash_cells))

    return list(found.values())


def _nearby_by_name(
    session: Session, normalized: NormalizedBusiness, cells: tuple[str, ...]
) -> list[Business]:
    """Businesses in the same or a neighbouring cell whose name is close enough to matter."""
    threshold = get_settings().resolution_block_name_ratio
    rows = list(
        session.scalars(
            select(Business)
            .where(Business.geohash7.in_(cells))
            .order_by(Business.created_at.asc(), Business.id.asc())
            .limit(GEO_FETCH_LIMIT)
        )
    )
    if not normalized.normalized_name:
        return []
    return [
        row
        for row in rows
        if row.normalized_name
        and fuzz.token_set_ratio(normalized.normalized_name, row.normalized_name) >= threshold
    ]
