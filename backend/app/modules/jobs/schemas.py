"""Search-job and job-run request/response models."""

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.jobs.models import JobRunStatus, SearchJobStatus


class GeoSpec(BaseModel):
    """A search area. Either a named place (`city` + `state`) or a radius around a point."""

    model_config = ConfigDict(extra="forbid")

    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int | None = Field(default=None, ge=1, le=200_000)

    @model_validator(mode="after")
    def _one_complete_form(self) -> Self:
        named = self.city is not None and self.state is not None
        point = self.lat is not None and self.lng is not None and self.radius_m is not None
        if not (named or point):
            raise ValueError(
                "geo must supply either 'city' and 'state', or 'lat', 'lng' and 'radius_m'"
            )
        return self


class SearchJobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    geo: GeoSpec
    industry: str = Field(min_length=1, max_length=120)
    source_ids: list[uuid.UUID] = Field(default_factory=list)
    status: SearchJobStatus = SearchJobStatus.draft
    # How many results one run asks for. Null = PLACES_MAX_RESULTS_PER_JOB; never above it.
    max_results: int | None = Field(default=None, ge=1)


class SearchJobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    geo: GeoSpec | None = None
    industry: str | None = Field(default=None, min_length=1, max_length=120)
    source_ids: list[uuid.UUID] | None = None
    status: SearchJobStatus | None = None
    max_results: int | None = Field(default=None, ge=1)


class SearchJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    geo: dict[str, object]
    industry: str
    source_ids: list[uuid.UUID]
    status: SearchJobStatus
    max_results: int | None = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class JobRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    search_job_id: uuid.UUID | None
    kind: str
    status: JobRunStatus
    progress_total: int
    progress_done: int
    attempts: int
    error: str | None
    cancel_requested: bool
    result_summary: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class IndustryOption(BaseModel):
    """One entry of the industry dropdown: our slug, a label, and the text the source
    is asked for (`industry` on the search job)."""

    key: str
    label: str
    query: str


class EstimateRequest(BaseModel):
    max_results: int | None = Field(default=None, ge=1)
    # Which sources the run would use; empty = Google Places.
    source_ids: list[uuid.UUID] = Field(default_factory=list)


class CostEstimate(BaseModel):
    """What one run is expected to cost before it is started (spec v0.8.0 §6).

    Every number is an upper bound from the caps, never a promise: a PageSpeed call
    happens only for a reachable site, an AI call only for a site with text.
    """

    max_results: int
    uses_places: bool
    places_calls: int
    places_used_today: int
    places_daily_cap: int
    places_remaining_today: int
    pagespeed_calls: int
    pagespeed_used_today: int
    pagespeed_daily_cap: int
    pagespeed_remaining_today: int
    ai_enabled: bool
    ai_provider: str
    ai_calls: int
    ai_calls_used_today: int
    ai_daily_call_cap: int
    ai_budget_usd: float
    ai_spent_today_usd: float
    ai_budget_remaining_usd: float
    can_run: bool
    blockers: list[str]


class SearchJobListItem(SearchJobRead):
    """A search job with its most recent discovery run, for the searches page."""

    last_run: JobRunRead | None = None


class PipelineStage(BaseModel):
    stage: str
    run: JobRunRead | None
    counts: dict[str, Any]
    error: str | None


class PipelineRead(BaseModel):
    """One run of a search job followed through the four stages."""

    search_job_id: uuid.UUID
    discovery_run_id: uuid.UUID | None
    stages: list[PipelineStage]
    # Query-string parameters that open the review queue on this search's businesses.
    review_queue_query: dict[str, str]
