"""Read models for discovered records, sightings and runs."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.modules.adapters.base import Candidate


class RecordSightingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_run_id: uuid.UUID
    search_job_id: uuid.UUID | None
    seen_at: datetime
    rank: int


class DiscoveredRecordSummary(BaseModel):
    """The list view: the mapped candidate fields plus provenance, no raw payload."""

    id: uuid.UUID
    source_id: uuid.UUID
    source: str
    source_record_id: str
    source_url: str | None
    display_name: str | None
    formatted_address: str | None
    phone: str | None
    website: str | None
    business_status: str | None
    first_discovered_at: datetime
    last_discovered_at: datetime
    purged_at: datetime | None
    created_at: datetime


class DiscoveredRecordDetail(DiscoveredRecordSummary):
    """The detail view: everything, including the payload exactly as it was received."""

    raw_payload: dict[str, Any] | None
    payload_hash: str | None
    business_id: uuid.UUID | None
    published_at: datetime | None
    extracted_at: datetime | None
    evidence_text: str | None
    confidence: Decimal | None
    content_expires_at: datetime | None
    types: list[str] | None
    lat: float | None
    lng: float | None
    sightings: list[RecordSightingRead]


class DiscoveryResultSummary(BaseModel):
    """What a finished discovery run reports, stored on `job_runs.result_summary`."""

    fetched: int = 0
    stored_new: int = 0
    updated: int = 0
    invalid: int = 0
    api_calls: int = 0


def candidate_fields(candidate: Candidate) -> dict[str, Any]:
    return {
        "display_name": candidate.display_name,
        "formatted_address": candidate.formatted_address,
        "phone": candidate.phone,
        "website": candidate.website,
        "business_status": candidate.business_status,
        "types": candidate.types,
        "lat": candidate.lat,
        "lng": candidate.lng,
    }


class DataProviderRead(BaseModel):
    """A third-party data provider the source says must be shown with its result.

    Places returns these as `attributions[]` ("A set of data provider that must be shown
    with this result"); they are shown beside the Google Maps attribution wherever the
    business's Places data is (spec v0.11.1). Most places carry none.
    """

    provider: str
    provider_uri: str | None
