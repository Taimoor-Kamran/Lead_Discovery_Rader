"""The one shape every source is reduced to before entity resolution sees it."""

import enum

from pydantic import BaseModel, ConfigDict, Field


class WebsiteKind(enum.StrEnum):
    own_site = "own_site"
    builder_subdomain = "builder_subdomain"
    social_profile = "social_profile"
    none = "none"


class BusinessStatus(enum.StrEnum):
    operational = "operational"
    closed_temporarily = "closed_temporarily"
    closed_permanently = "closed_permanently"
    unknown = "unknown"


class Address(BaseModel):
    """Every part optional: a missing component is `None`, never filled in from elsewhere."""

    model_config = ConfigDict(frozen=True)

    line1: str | None = None
    line2: str | None = None
    street_key: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class NormalizedBusiness(BaseModel):
    """One record, cleaned. Validation failures mark the record `invalid`, never dropped.

    `display_name` is the only required field: a record nobody can name is not a business
    we can show a human, so it fails validation and says why.
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    normalized_name: str | None = None
    name_key: str | None = None
    legal_name: str | None = None
    industry: str = "other"
    raw_types: list[str] = Field(default_factory=list)
    address: Address = Address()
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    geohash7: str | None = None
    phone_e164: str | None = None
    website: str | None = None
    domain: str | None = None
    website_kind: WebsiteKind = WebsiteKind.none
    business_status: BusinessStatus = BusinessStatus.unknown
    # The listing's own star rating (1 to 5) and review count, as the source reports them.
    rating: float | None = None
    user_rating_count: int | None = None
