"""Request and response models for the review queue, decisions, undo and leads."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.audit_web.models import AuditStatus
from app.modules.audit_web.schemas import WebsiteAuditDetail
from app.modules.businesses.schemas import BusinessDetail
from app.modules.compliance.schemas import SuppressionRead
from app.modules.crm.schemas import CrmLeadStatusRead, CrmSyncAttemptRead
from app.modules.opportunities.models import OpportunitySource, ReviewStatus
from app.modules.opportunities.schemas import OpportunityDetail
from app.modules.review.models import Decision

# The reasons a reviewer may give. Free text goes in `note`; `other` requires one.
REJECT_REASON_CODES = ("evidence_wrong", "business_closed", "wrong_industry", "ai_mistake", "other")
NOT_A_FIT_REASON_CODES = ("too_small", "too_large", "outside_area", "already_client", "other")
BATCH_MAX_IDS = 50
NOTE_MAX = 2000


class ReviewRequest(BaseModel):
    """One decision on one opportunity. Which extra fields are required depends on it."""

    decision: Decision
    lock_version: int = Field(ge=0)
    reason_code: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    duplicate_of: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None


class BatchReviewRequest(BaseModel):
    """Reject or not-a-fit for up to 50 opportunities at once. Nothing else is batchable."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=BATCH_MAX_IDS)
    decision: Literal["reject", "not_a_fit"]
    reason_code: str = Field(max_length=64)
    note: str | None = Field(default=None, max_length=NOTE_MAX)


class BatchItemResult(BaseModel):
    id: uuid.UUID
    result: Literal["ok", "conflict", "not_allowed"]
    review_status: ReviewStatus | None
    lock_version: int | None
    message: str | None


class BatchReviewResult(BaseModel):
    decided: int
    items: list[BatchItemResult]


class ReviewDecisionRead(BaseModel):
    id: uuid.UUID
    opportunity_id: uuid.UUID
    decision: Decision
    from_status: ReviewStatus
    to_status: ReviewStatus
    reason_code: str | None
    note: str | None
    duplicate_of: uuid.UUID | None
    assigned_to: uuid.UUID | None
    assigned_to_email: str | None
    decided_by: uuid.UUID
    decided_by_email: str | None
    decided_at: datetime
    undone_at: datetime | None
    undone_by: uuid.UUID | None
    # Until when an undo is accepted, and whether *this* caller may still do it.
    undo_until: datetime
    can_undo: bool


class DecidedOpportunity(OpportunityDetail):
    """What a decision endpoint returns: the updated opportunity and the row it wrote."""

    decision: ReviewDecisionRead


class UndoResult(BaseModel):
    undone: list[ReviewDecisionRead]
    opportunities: list[OpportunityDetail]
    suppressions_lifted: int


# --- provenance -----------------------------------------------------------------------------


class SourceRecordRead(BaseModel):
    """One discovered record a business was built from: the answer to "where did you get
    my details?", in the words a prospect asks it in.

    `code` is what the API and the `sources` table call it (`google_places`); `name` is the
    operator-facing name on the source row. The screen reads its own label from
    `lib/labels.ts` and keeps `code` in the tooltip.
    """

    code: str
    name: str
    source_record_id: str
    source_url: str | None
    # When the record was first discovered, and when the source last handed it to us again.
    discovered_at: datetime
    last_seen_at: datetime


class LinkedProfileRead(BaseModel):
    """A social profile the business links to **from its own homepage**.

    The v0.4.0 audit records which platforms the homepage points at. No profile is ever
    fetched, and this is never a source: `page_url` on the block below is the page the
    links were read on, so the screen can say so. `url` is null when the audit's evidence
    does not yield a link we would let a browser open — never a guess.
    """

    platform: str
    url: str | None


class LinkedProfilesRead(BaseModel):
    page_url: str | None
    profiles: list[LinkedProfileRead]


# --- queue --------------------------------------------------------------------------------


class QueueOpportunity(BaseModel):
    id: uuid.UUID
    service: str
    service_name: str
    source: OpportunitySource
    confidence: float
    score: float
    review_status: ReviewStatus
    lock_version: int
    reason: str
    weak: bool


class QueueAudit(BaseModel):
    status: AuditStatus
    audited_at: datetime
    top_findings: list[str]


class QueueItem(BaseModel):
    """One business in the queue with its open opportunities, best score first."""

    business_id: uuid.UUID
    display_name: str
    city: str | None
    state: str | None
    industry: str | None
    website: str | None
    # The listing's star rating and review count (v0.11.0); null where the source gave none.
    rating: float | None = None
    user_rating_count: int | None = None
    top_score: float
    latest_audit: QueueAudit | None
    opportunities: list[QueueOpportunity]
    weak_hidden: int
    # The distinct source codes behind the business, for the queue's source column.
    sources: list[str]


# --- detail -------------------------------------------------------------------------------


class AISummaryRead(BaseModel):
    """The model's summary of the business. Always flagged; never a fact."""

    ai_generated: Literal[True] = True
    classification_id: uuid.UUID
    model: str
    prompt_version: str
    status: str
    escalated: bool
    business_summary: str | None
    industry: str | None
    industry_matches_listing: bool | None
    buying_intent: str | None
    unknowns: list[str]
    created_at: datetime


class ReviewOpportunity(OpportunityDetail):
    service_name: str
    history: list[ReviewDecisionRead]
    weak: bool
    # `reason` split for display: the rules' wording and the model's rationale, apart.
    # Neither is reworded; a part that is not known is null.
    rule_reason: str | None
    ai_rationale: str | None


class ReviewDetail(BaseModel):
    """Everything a reviewer needs for one business, in one call."""

    business: BusinessDetail
    audit: WebsiteAuditDetail | None
    ai: AISummaryRead | None
    opportunities: list[ReviewOpportunity]
    suppressed: bool
    suppressions: list[SuppressionRead]
    undo_window_minutes: int
    weak_confidence: float
    # Where this came from: one entry per contributing discovered record, and what the
    # business's own homepage links to.
    sources: list[SourceRecordRead]
    linked_profiles: LinkedProfilesRead
    # Whether the AI layer is switched on at all. False means rules-only: the page shows no
    # AI furniture rather than an empty box where an AI answer would have gone.
    ai_enabled: bool


# --- leads --------------------------------------------------------------------------------


class LeadRead(BaseModel):
    """An approved, unsuppressed opportunity as a sales rep sees it. Business-level only."""

    opportunity_id: uuid.UUID
    business_id: uuid.UUID
    business_name: str
    city: str | None
    state: str | None
    industry: str | None
    service: str
    service_name: str
    score: float
    reason: str
    approved_by: uuid.UUID | None
    approved_by_email: str | None
    approved_at: datetime | None
    assigned_to: uuid.UUID | None
    assigned_to_email: str | None
    phone_e164: str | None
    website: str | None
    lock_version: int
    top_evidence: dict[str, Any] | None
    rule_reason: str | None
    ai_rationale: str | None
    # The distinct source codes behind the business, for the leads list's source column.
    sources: list[str]
    # Where the business's CRM record stands (v0.7.0). Null until a sync was scheduled.
    crm: CrmLeadStatusRead | None = None


class LeadDetail(BaseModel):
    """One approved lead, read-only: the business, the audit behind it, the claim itself
    and who approved it. A sales rep may only open a lead assigned to them."""

    lead: LeadRead
    business: BusinessDetail
    audit: WebsiteAuditDetail | None
    opportunity: ReviewOpportunity
    # Every CRM sync attempt for the business, newest first (v0.7.0).
    crm_history: list[CrmSyncAttemptRead] = []
    # The same two blocks the review page shows, so a rep can answer the same question.
    sources: list[SourceRecordRead]
    linked_profiles: LinkedProfilesRead
    ai_enabled: bool
