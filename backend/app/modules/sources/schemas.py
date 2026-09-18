"""Source read/update models."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.modules.sources.models import SourceKind


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: SourceKind
    config: dict[str, Any]
    enabled: bool
    created_at: datetime


class SourceUpdate(BaseModel):
    enabled: bool
