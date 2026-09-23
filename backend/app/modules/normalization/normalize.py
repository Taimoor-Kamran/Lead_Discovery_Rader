"""`normalize(candidate, source)` — the one entry point into normalization.

Everything a resolver compares comes out of here, so the rules live in one place and a
source can never smuggle its own formatting downstream.
"""

from pydantic import ValidationError

from app.modules.adapters.base import Candidate
from app.modules.normalization import addresses, names, phones, taxonomy
from app.modules.normalization.geo import geohash7
from app.modules.normalization.schemas import NormalizedBusiness
from app.modules.normalization.web import parse_website


class NormalizationError(ValueError):
    """The record cannot be described as a business. Carries the reasons, never a guess."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


def normalize(candidate: Candidate, source: str) -> NormalizedBusiness:
    """Reduce one source's candidate to the shared shape, or raise `NormalizationError`."""
    address = addresses.from_components(
        candidate.address_components, formatted=candidate.formatted_address
    )
    normalized_name = names.normalize_name(candidate.display_name)
    website, domain, website_kind = parse_website(candidate.website)

    lat, lng = _valid_point(candidate.lat, candidate.lng)

    try:
        return NormalizedBusiness(
            source=source,
            display_name=names.display_name(candidate.display_name) or "",
            normalized_name=normalized_name,
            name_key=names.name_key(normalized_name),
            industry=taxonomy.to_industry(candidate.primary_type, candidate.types),
            raw_types=list(candidate.types or []),
            address=address,
            lat=lat,
            lng=lng,
            geohash7=geohash7(lat, lng),
            phone_e164=phones.to_e164(candidate.phone, region=address.country),
            website=website,
            domain=domain,
            website_kind=website_kind,
            business_status=taxonomy.to_business_status(candidate.business_status),
            rating=_valid_rating(candidate.rating),
            user_rating_count=_valid_count(candidate.user_rating_count),
        )
    except ValidationError as exc:
        raise NormalizationError(
            "The record cannot be normalized into a business",
            errors=[f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()],
        ) from exc


def _valid_point(lat: float | None, lng: float | None) -> tuple[float | None, float | None]:
    """A half-known or out-of-range coordinate is no coordinate at all."""
    if lat is None or lng is None:
        return None, None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return None, None
    return lat, lng


def _valid_rating(value: float | None) -> float | None:
    """A Places rating is 1.0 to 5.0. Anything else is not a rating we can show: `None`."""
    if value is None or isinstance(value, bool) or not 1.0 <= float(value) <= 5.0:
        return None
    return round(float(value), 1)


def _valid_count(value: int | None) -> int | None:
    if value is None or isinstance(value, bool) or value < 0:
        return None
    return int(value)
