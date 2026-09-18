"""Request and response models for the review queue and the resolution run summary."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from app.modules.businesses.schemas import BusinessSummary
from app.modules.discovery.schemas import DiscoveredRecordSummary
from app.modules.resolution.models import MatchCandidateStatus


class ResolutionResultSummary(BaseModel):
    """What a finished resolution run reports, stored on `job_runs.result_summary`."""

    processed: int = 0
    linked_existing: int = 0
    created: int = 0
    needs_review: int = 0
    invalid: int = 0


class MatchCandidateDetail(BaseModel):
    """Both sides of a pending pair, plus the score and every signal behind it."""

    id: uuid.UUID
    status: MatchCandidateStatus
    score: Decimal
    signals: dict[str, Any]
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime
    discovered_record_id: uuid.UUID
    business_id: uuid.UUID
    record: DiscoveredRecordSummary | None
    business: BusinessSummary | None


class MatchDecisionRequest(BaseModel):
    decision: Literal["merge", "keep_apart"]
