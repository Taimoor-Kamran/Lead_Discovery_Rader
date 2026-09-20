"""Alert read model."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule: str
    severity: str
    message: str
    details: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: uuid.UUID | None
    cleared_at: datetime | None
    active: bool
    acknowledged: bool
