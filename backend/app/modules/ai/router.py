"""`/ai/classifications/{id}` and `/ai/usage`: what the model said, and what it cost."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.modules.ai.models import AIClassification, ClassificationStatus
from app.modules.auth.deps import DbSession, require_role
from app.modules.auth.models import Role, User

ai_router = APIRouter(prefix="/ai", tags=["ai"])

# Raw model output and spend are for the roles that operate the system.
AIOperator = Annotated[User, Depends(require_role(Role.tech_admin))]

# Statuses that mean a model was actually called.
CALLED_STATUSES = (
    ClassificationStatus.ok,
    ClassificationStatus.guardrail_trimmed,
    ClassificationStatus.schema_invalid,
    ClassificationStatus.error,
)


class AIClassificationRead(BaseModel):
    id: uuid.UUID
    business_id: uuid.UUID
    website_audit_id: uuid.UUID
    job_run_id: uuid.UUID | None
    model: str
    prompt_version: str
    input_hash: str
    status: ClassificationStatus
    escalated: bool
    output: dict[str, Any] | None
    raw_output: str | None
    rejected_claims: list[dict[str, Any]]
    tokens_in: int
    tokens_out: int
    est_cost_usd: float | None
    latency_ms: int
    error: str | None
    content_expires_at: datetime | None
    purged_at: datetime | None
    created_at: datetime


class AIUsageRead(BaseModel):
    """One UTC day of AI use, from the stored classifications."""

    date: date
    provider: str
    calls: int
    escalations: int
    reused: int
    skipped_budget: int
    schema_invalid: int
    errors: int
    tokens_in: int
    tokens_out: int
    est_cost_usd: float | None
    prices_configured: bool
    budget_usd: float
    budget_remaining_usd: float | None
    daily_call_cap: int


@ai_router.get("/classifications/{classification_id}", response_model=AIClassificationRead)
def get_classification(
    classification_id: uuid.UUID, actor: AIOperator, session: DbSession
) -> AIClassificationRead:
    """The raw text, the validated output and every claim the guardrails removed."""
    row = session.get(AIClassification, classification_id)
    if row is None:
        raise NotFoundError(
            "AI classification not found", details={"classification_id": str(classification_id)}
        )
    return AIClassificationRead(
        id=row.id,
        business_id=row.business_id,
        website_audit_id=row.website_audit_id,
        job_run_id=row.job_run_id,
        model=row.model,
        prompt_version=row.prompt_version,
        input_hash=row.input_hash,
        status=row.status,
        escalated=row.escalated,
        output=row.output,
        raw_output=row.raw_output,
        rejected_claims=list(row.rejected_claims or []),
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        est_cost_usd=float(row.est_cost_usd) if row.est_cost_usd is not None else None,
        latency_ms=row.latency_ms,
        error=row.error,
        content_expires_at=row.content_expires_at,
        purged_at=row.purged_at,
        created_at=row.created_at,
    )


@ai_router.get("/usage", response_model=AIUsageRead)
def get_usage(
    actor: AIOperator,
    session: DbSession,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> AIUsageRead:
    """Calls, escalations, tokens and estimated cost for one UTC day (today by default)."""
    return usage_for(session, day or datetime.now(UTC).date())


def usage_for(session: Session, day: date) -> AIUsageRead:
    settings = get_settings()
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    in_day = (AIClassification.created_at >= start, AIClassification.created_at < end)

    def count(*conditions: Any) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(AIClassification).where(*in_day, *conditions)
            )
            or 0
        )

    calls = count(AIClassification.status.in_(CALLED_STATUSES))
    escalations = count(
        AIClassification.status.in_(CALLED_STATUSES), AIClassification.escalated.is_(True)
    )
    sums = session.execute(
        select(
            func.coalesce(func.sum(AIClassification.tokens_in), 0),
            func.coalesce(func.sum(AIClassification.tokens_out), 0),
            func.sum(AIClassification.est_cost_usd),
        ).where(*in_day)
    ).one()
    tokens_in, tokens_out, cost = int(sums[0]), int(sums[1]), sums[2]
    priced = count(AIClassification.est_cost_usd.is_not(None))
    est_cost = float(Decimal(cost)) if cost is not None and priced else None
    budget = float(settings.ai_daily_budget_usd)
    return AIUsageRead(
        date=day,
        provider=settings.resolved_ai_provider,
        calls=calls,
        escalations=escalations,
        reused=count(AIClassification.status == ClassificationStatus.reused),
        skipped_budget=count(AIClassification.status == ClassificationStatus.skipped_budget),
        schema_invalid=count(AIClassification.status == ClassificationStatus.schema_invalid),
        errors=count(AIClassification.status == ClassificationStatus.error),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        est_cost_usd=est_cost,
        prices_configured=settings.ai_triage_price_in_per_m is not None
        and settings.ai_triage_price_out_per_m is not None,
        budget_usd=budget,
        budget_remaining_usd=(
            round(max(budget - (est_cost or 0.0), 0.0), 6) if est_cost is not None else None
        ),
        daily_call_cap=settings.ai_daily_call_cap,
    )
