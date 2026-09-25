"""Request and response models for suppressions."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.compliance.models import SuppressionSource
from app.modules.discovery.schemas import DataProviderRead


class SuppressionCreate(BaseModel):
    """At least one of the three targets. A business also brings its domain and phone."""

    business_id: uuid.UUID | None = None
    domain: str | None = Field(default=None, max_length=253)
    phone_e164: str | None = Field(default=None, max_length=32)
    reason: str = Field(min_length=1, max_length=2000)


class SuppressionRead(BaseModel):
    id: uuid.UUID
    business_id: uuid.UUID | None
    business_name: str | None
    domain: str | None
    phone_e164: str | None
    reason: str
    source: SuppressionSource
    created_by: uuid.UUID
    created_at: datetime
    lifted_at: datetime | None
    lifted_by: uuid.UUID | None
    active: bool
    # Third-party data providers Places requires shown with the business (v0.11.1).
    data_providers: list[DataProviderRead] = []
