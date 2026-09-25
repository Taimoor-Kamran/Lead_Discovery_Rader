"""Read models for businesses and their field-level provenance."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.modules.audit_web.schemas import LatestAuditRead
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind


class BusinessSummary(BaseModel):
    """The list view. Every value here is a survivorship winner, not a stored fact."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    normalized_name: str | None
    industry: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str | None
    phone_e164: str | None
    website: str | None
    domain: str | None
    website_kind: WebsiteKind
    business_status: BusinessStatus
    # The listing's star rating and review count; null where the source gave none.
    rating: float | None = None
    user_rating_count: int | None = None
    created_at: datetime
    updated_at: datetime
    # The newest website audit, when there is one. Null means "never audited", which is
    # different from an audit that found nothing.
    latest_audit: LatestAuditRead | None = None


class FieldValueRead(BaseModel):
    """Who said what, when, and until when it may be kept."""

    field: str
    value: str | None
    source: str
    source_id: uuid.UUID
    discovered_record_id: uuid.UUID
    observed_at: datetime
    expires_at: datetime | None
    purged_at: datetime | None
    is_displayed: bool


class LinkedRecordRead(BaseModel):
    id: uuid.UUID
    source: str
    source_record_id: str
    source_url: str | None
    first_discovered_at: datetime
    last_discovered_at: datetime
    resolution_status: str


class BusinessDetail(BusinessSummary):
    """The detail view: the whole address, the coordinates and the full provenance trail."""

    legal_name: str | None
    name_key: str | None
    address_line1: str | None
    address_line2: str | None
    street_key: str | None
    lat: float | None
    lng: float | None
    geohash7: str | None
    places_content_expires_at: datetime | None
    field_values: list[FieldValueRead]
    records: list[LinkedRecordRead]
