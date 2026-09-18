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


class SearchJobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    geo: GeoSpec | None = None
    industry: str | None = Field(default=None, min_length=1, max_length=120)
    source_ids: list[uuid.UUID] | None = None
    status: SearchJobStatus | None = None


class SearchJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    geo: dict[str, object]
    industry: str
    source_ids: list[uuid.UUID]
    status: SearchJobStatus
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
