"""Read models for opportunities and the `classification` run summary."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.modules.opportunities.models import OpportunitySource, ReviewStatus


class ScoreComponentsRead(BaseModel):
    facts: float
    inference: float
    intent: float
    contactability: float


class AIProvenanceRead(BaseModel):
    """Which model said what, and whether it agreed with the rules."""

    classification_id: uuid.UUID
    model: str
    prompt_version: str
    status: str
    escalated: bool
    buying_intent: str | None
    business_summary: str | None


class OpportunitySummary(BaseModel):
    """The list view: enough to rank and pick, with the strongest piece of evidence."""

    id: uuid.UUID
    business_id: uuid.UUID
    business_name: str
    industry: str | None
    city: str | None
    state: str | None
    service: str
    source: OpportunitySource
    confidence: float
    ai_agrees: bool | None
    score: float
    score_components: ScoreComponentsRead
    scoring_version: str
    review_status: ReviewStatus
    # Echoed back with every decision; a stale value is a 409 (v0.6.0).
    lock_version: int
    top_evidence: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class OpportunityDetail(OpportunitySummary):
    """The whole claim: reason, every evidence item, and where the AI's part came from."""

    reason: str
    evidence: list[dict[str, Any]]
    website_audit_id: uuid.UUID | None
    ai_classification_id: uuid.UUID | None
    ai: AIProvenanceRead | None
    assigned_to: uuid.UUID | None
    decided_at: datetime | None
    decided_by: uuid.UUID | None


class ClassificationResultSummary(BaseModel):
    """What one `classification` run did, written to `job_runs.result_summary`."""

    businesses: int = 0
    opportunities_created: int = 0
    opportunities_updated: int = 0
    ai_calls: int = 0
    ai_escalations: int = 0
    ai_reused: int = 0
    ai_skipped_budget: int = 0
    ai_errors: int = 0
    est_cost_usd: float = 0.0
