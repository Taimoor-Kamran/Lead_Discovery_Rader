"""Classifying one business, merging rules with AI, scoring, and reading opportunities back.

The order inside `classify` is the order of trust:

1. **rules** — deterministic, always run, never need a model;
2. **AI** — only when enabled, budgeted and there is page text; every answer goes through
   the schema and the guardrails, and an answer that fails them is recorded, not used;
3. **merge** — the AI may add to what the rules found or raise a confidence a little with
   valid evidence. It can never remove a rule opportunity;
4. **score** — four components, always stored;
5. **upsert** — one open opportunity per business and service, updated in place.

An AI failure of any kind leaves the rule opportunities standing. Nothing here approves,
contacts or exports anything.

What a human decided outranks all of it (v0.6.0): a suppressed business gets nothing at
all, an approved opportunity is never overwritten, and a service a reviewer rejected,
marked not-a-fit or duplicate is not re-created as pending until the cool-down has passed.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.modules.ai import guardrails, routing
from app.modules.ai.budget import AIBudget, estimate_cost
from app.modules.ai.client import LLMClient, LLMError, LLMRequest, Tier
from app.modules.ai.fake_client import FAKE_ESCALATION_MODEL, FAKE_TRIAGE_MODEL, FakeLLMClient
from app.modules.ai.models import REUSABLE_STATUSES, AIClassification, ClassificationStatus
from app.modules.ai.prompt import ClassificationInput, build_input, input_hash, render
from app.modules.ai.schema import (
    PROMPT_VERSION,
    SCHEMA_NAME,
    AIOutput,
    SchemaDriftError,
    json_schema,
    parse_output,
)
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.audit_web.service import latest_audit
from app.modules.businesses.models import Business
from app.modules.compliance.service import is_suppressed
from app.modules.jobs.models import JobRun as JobRunType
from app.modules.normalization.schemas import BusinessStatus
from app.modules.opportunities.catalogue import (
    AI_MAX_RAISE,
    AI_ONLY_MAX_CONFIDENCE,
    service_keys,
)
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from app.modules.opportunities.rules import RuleOpportunity, rule_opportunities
from app.modules.opportunities.schemas import (
    AIProvenanceRead,
    OpportunityDetail,
    OpportunitySummary,
    ScoreComponentsRead,
)
from app.modules.opportunities.scoring import SCORING_VERSION, Score, Weights, score

logger = get_logger("app.opportunities")

CLASSIFICATION_JOB_KIND = "classification"
AUDIT_JOB_KIND = "audit"
# The neutral reason an AI-only opportunity gets when its rationale was blanked.
AI_ONLY_DEFAULT_REASON = "Suggested from the homepage text; see the quoted evidence."
# What a classification row says for `model` when no model was called.
NO_MODEL = "none"
PLACES = 3


# --- tools --------------------------------------------------------------------------------


@dataclass
class ClassificationTools:
    """The provider, the budget and the per-run call counter. Injected so tests drive them."""

    llm: LLMClient | None
    budget: AIBudget | None
    settings: Settings = field(default_factory=get_settings)
    provider: str = "disabled"
    calls_made: int = 0

    @classmethod
    def build(
        cls,
        *,
        job_run_id: uuid.UUID | None = None,
        settings: Settings | None = None,
        redis_client: Any = None,
    ) -> "ClassificationTools":
        config = settings or get_settings()
        provider = config.resolved_ai_provider
        llm: LLMClient | None
        if provider == "openai":
            from app.modules.ai.openai_client import build_openai_client

            llm = build_openai_client(
                job_run_id=job_run_id, settings=config, redis_client=redis_client
            )
        elif provider == "fake":
            llm = FakeLLMClient()
        else:
            llm = None
        if redis_client is None:
            from app.core.redis import get_redis

            redis_client = get_redis()
        return cls(
            llm=llm,
            budget=AIBudget(redis_client, settings=config) if llm is not None else None,
            settings=config,
            provider=provider,
        )

    @property
    def ai_enabled(self) -> bool:
        return self.llm is not None and self.budget is not None

    def model_for(self, tier: Tier) -> str:
        configured = (
            self.settings.ai_triage_model if tier == "triage" else self.settings.ai_escalation_model
        )
        if configured:
            return configured
        if self.provider == "fake":
            return FAKE_TRIAGE_MODEL if tier == "triage" else FAKE_ESCALATION_MODEL
        return ""

    @property
    def escalation_available(self) -> bool:
        return self.settings.ai_escalation_enabled and bool(self.model_for("escalation"))

    def cost_of(self, tier: Tier, *, tokens_in: int, tokens_out: int) -> Decimal | None:
        if self.provider == "fake":
            return Decimal(0)
        return estimate_cost(self.settings, tier, tokens_in=tokens_in, tokens_out=tokens_out)


# --- results ------------------------------------------------------------------------------


@dataclass
class TierResult:
    """What one tier's attempt (triage or escalation) came to."""

    tier: Tier
    output: AIOutput | None = None
    classification: AIClassification | None = None
    schema_invalid: bool = False
    reused: bool = False
    skipped_budget: bool = False
    errored: bool = False
    calls: int = 0
    cost: Decimal = Decimal(0)


@dataclass
class AIRun:
    """The AI step for one business, summed over both tiers."""

    output: AIOutput | None = None
    classification: AIClassification | None = None
    escalated: bool = False
    calls: int = 0
    reused: int = 0
    skipped_budget: int = 0
    errors: int = 0
    cost: Decimal = Decimal(0)
    reasons: tuple[str, ...] = ()


@dataclass
class ClassificationOutcome:
    business_id: uuid.UUID
    website_audit_id: uuid.UUID | None
    opportunities: list[Opportunity] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    ai: AIRun | None = None


# --- classifying one business ----------------------------------------------------------


def classify(
    session: Session,
    business: Business,
    *,
    tools: ClassificationTools,
    job_run_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> ClassificationOutcome:
    """Rules → (AI) → merge → score → upsert, for one business. Never raises for the AI."""
    audit = latest_audit(session, business.id)
    if audit is None:
        return ClassificationOutcome(business_id=business.id, website_audit_id=None)
    if is_suppressed(session, business):
        # Do-not-contact (or an admin suppression) means no new pending opportunity, ever,
        # and no model call either: nothing about this business is work for anyone.
        logger.info(
            "business is suppressed; not classified", extra={"business_id": str(business.id)}
        )
        return ClassificationOutcome(business_id=business.id, website_audit_id=audit.id)

    rules = rule_opportunities(business, audit)
    outcome = ClassificationOutcome(business_id=business.id, website_audit_id=audit.id)
    if _no_opportunities(business, audit):
        return outcome

    ai_run = _run_ai(session, business, audit, tools=tools, job_run_id=job_run_id, now=now)
    outcome.ai = ai_run
    merged = merge(rules, ai_run.output if ai_run is not None else None)
    intent_explicit = bool(
        ai_run is not None
        and ai_run.output is not None
        and ai_run.output.buying_intent == "explicit"
    )
    outcome.opportunities, outcome.created, outcome.updated = upsert_opportunities(
        session,
        business,
        audit,
        merged,
        classification=ai_run.classification if ai_run is not None else None,
        intent_explicit=intent_explicit,
        settings=tools.settings,
        now=now,
    )
    logger.info(
        "business classified",
        extra={
            "business_id": str(business.id),
            "services": [o.service for o in outcome.opportunities],
            "ai_status": (
                ai_run.classification.status.value
                if ai_run is not None and ai_run.classification is not None
                else None
            ),
        },
    )
    return outcome


def _no_opportunities(business: Business, audit: WebsiteAudit) -> bool:
    """Businesses the spec says get nothing: closed for good, or a site that refused us."""
    return (
        business.business_status is BusinessStatus.closed_permanently
        or audit.status is AuditStatus.robots_blocked
        or "robots_blocked" in audit.finding_codes
    )


def _ai_eligible(audit: WebsiteAudit) -> bool:
    return audit.status is AuditStatus.done and bool(audit.page_text)


def _run_ai(
    session: Session,
    business: Business,
    audit: WebsiteAudit,
    *,
    tools: ClassificationTools,
    job_run_id: uuid.UUID | None,
    now: datetime | None,
) -> AIRun | None:
    """The AI step: triage, maybe escalation, both recorded. `None` when nothing was tried."""
    if not _ai_eligible(audit):
        return None
    if not tools.ai_enabled:
        row = _record(
            session,
            business,
            audit,
            job_run_id=job_run_id,
            model=NO_MODEL,
            input_hash_value="",
            status=ClassificationStatus.skipped_disabled,
            error=f"AI provider is '{tools.provider}'",
            now=now,
        )
        return AIRun(classification=row)

    run = AIRun()
    try:
        classification_input = build_input(business, audit, settings=tools.settings)
        context = guardrails.GuardrailContext.build(
            corpus=classification_input.corpus(),
            finding_codes=classification_input.finding_codes,
            urls=classification_input.urls(),
            industries=classification_input.industries,
            intent_patterns=tools.settings.ai_explicit_intent_patterns,
            page_url=classification_input.page_url,
        )
        triage = _attempt_tier(
            session,
            business,
            audit,
            classification_input,
            context,
            tier="triage",
            tools=tools,
            job_run_id=job_run_id,
            now=now,
        )
        _absorb(run, triage)

        decision = routing.decide(
            output=triage.output,
            schema_invalid=triage.schema_invalid,
            finding_codes=classification_input.finding_codes,
            enabled=tools.escalation_available and not triage.skipped_budget,
        )
        run.reasons = decision.reasons
        if decision.escalate:
            escalation = _attempt_tier(
                session,
                business,
                audit,
                classification_input,
                context,
                tier="escalation",
                tools=tools,
                job_run_id=job_run_id,
                now=now,
                reasons=decision.reasons,
            )
            _absorb(run, escalation)
            # Counted only when the stronger model was actually called: a budget skip and a
            # reused answer both cost nothing and are not escalations.
            run.escalated = not escalation.skipped_budget and not escalation.reused
            if escalation.output is not None or escalation.schema_invalid:
                # The escalation answer replaces the triage answer — including a still
                # invalid one, which leaves the rules on their own.
                run.output = escalation.output
                run.classification = escalation.classification or run.classification
    except Exception:
        # A bug in our own AI plumbing must not cost the business its rule opportunities.
        logger.exception("the AI step failed", extra={"business_id": str(business.id)})
        run.errors += 1
        run.output = None
    return run


def _absorb(run: AIRun, result: TierResult) -> None:
    run.calls += result.calls
    run.cost += result.cost
    run.reused += 1 if result.reused else 0
    run.skipped_budget += 1 if result.skipped_budget else 0
    run.errors += 1 if result.errored else 0
    if result.output is not None:
        run.output = result.output
    if result.classification is not None:
        run.classification = result.classification


def _attempt_tier(
    session: Session,
    business: Business,
    audit: WebsiteAudit,
    classification_input: ClassificationInput,
    context: guardrails.GuardrailContext,
    *,
    tier: Tier,
    tools: ClassificationTools,
    job_run_id: uuid.UUID | None,
    now: datetime | None,
    reasons: tuple[str, ...] = (),
) -> TierResult:
    """One tier: reuse if possible, budget check, one call plus one retry on schema drift."""
    assert tools.llm is not None and tools.budget is not None  # `ai_enabled` was checked
    model = tools.model_for(tier)
    escalated = tier == "escalation"
    result = TierResult(tier=tier)
    hash_value = input_hash(model, classification_input)

    previous = find_reusable(session, hash_value)
    if previous is not None and previous.output is not None:
        result.output = AIOutput.model_validate(previous.output)
        result.reused = True
        result.classification = _record(
            session,
            business,
            audit,
            job_run_id=job_run_id,
            model=model,
            input_hash_value=hash_value,
            status=ClassificationStatus.reused,
            escalated=escalated,
            output=previous.output,
            rejected_claims=list(previous.rejected_claims or []),
            est_cost_usd=Decimal(0),
            error=f"reused classification {previous.id}",
            now=now,
        )
        return result

    decision = tools.budget.check(run_calls=tools.calls_made)
    if not decision.allowed:
        result.skipped_budget = True
        result.classification = _record(
            session,
            business,
            audit,
            job_run_id=job_run_id,
            model=model,
            input_hash_value=hash_value,
            status=ClassificationStatus.skipped_budget,
            escalated=escalated,
            error=decision.reason,
            now=now,
        )
        return result

    system, user = render(classification_input)
    request = LLMRequest(
        model=model,
        tier=tier,
        system=system,
        user=user,
        schema_name=SCHEMA_NAME,
        json_schema=json_schema(service_keys()),
        fixture_key=business.domain or business.display_name,
        metadata={"business_id": str(business.id), "escalation_reasons": list(reasons)},
    )

    tokens_in = tokens_out = latency = 0
    raw_text = ""
    drift: str | None = None
    parsed: AIOutput | None = None
    for _attempt in range(2):
        try:
            answer = tools.llm.complete(request)
        except LLMError as exc:
            result.errored = True
            result.classification = _record(
                session,
                business,
                audit,
                job_run_id=job_run_id,
                model=model,
                input_hash_value=hash_value,
                status=ClassificationStatus.error,
                escalated=escalated,
                raw_output=raw_text or None,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                est_cost_usd=result.cost if result.calls else None,
                # A failed call still took time; without its own latency the row read 0
                # for every one of the production 400s (spec v0.9.0).
                latency_ms=latency + exc.latency_ms,
                error=str(exc),
                now=now,
            )
            return result
        tools.calls_made += 1
        result.calls += 1
        cost = tools.cost_of(tier, tokens_in=answer.tokens_in, tokens_out=answer.tokens_out)
        tools.budget.record(cost)
        result.cost += cost or Decimal(0)
        tokens_in += answer.tokens_in
        tokens_out += answer.tokens_out
        latency += answer.latency_ms
        raw_text = answer.text
        try:
            parsed = parse_output(answer.text)
            break
        except SchemaDriftError as exc:
            drift = exc.reason
            logger.warning(
                "AI output did not match the schema",
                extra={"business_id": str(business.id), "tier": tier, "reason": drift},
            )

    cost_value = result.cost if tools.cost_of(tier, tokens_in=1, tokens_out=1) is not None else None
    if parsed is None:
        result.schema_invalid = True
        result.classification = _record(
            session,
            business,
            audit,
            job_run_id=job_run_id,
            model=model,
            input_hash_value=hash_value,
            status=ClassificationStatus.schema_invalid,
            escalated=escalated,
            raw_output=raw_text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            est_cost_usd=cost_value,
            latency_ms=latency,
            error=drift,
            now=now,
        )
        return result

    checked = guardrails.apply(parsed, context)
    result.output = checked.output
    result.classification = _record(
        session,
        business,
        audit,
        job_run_id=job_run_id,
        model=model,
        input_hash_value=hash_value,
        status=(
            ClassificationStatus.guardrail_trimmed if checked.trimmed else ClassificationStatus.ok
        ),
        escalated=escalated,
        output=checked.output.model_dump(mode="json"),
        raw_output=raw_text,
        rejected_claims=checked.rejected_claims,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        est_cost_usd=cost_value,
        latency_ms=latency,
        error=f"escalated: {', '.join(reasons)}" if reasons else None,
        now=now,
    )
    return result


def find_reusable(session: Session, hash_value: str) -> AIClassification | None:
    """The newest successful classification of exactly this input with this model."""
    if not hash_value:
        return None
    return session.scalars(
        select(AIClassification)
        .where(
            AIClassification.input_hash == hash_value,
            AIClassification.status.in_(list(REUSABLE_STATUSES)),
            AIClassification.output.is_not(None),
        )
        .order_by(AIClassification.created_at.desc(), AIClassification.id.desc())
        .limit(1)
    ).first()


def _record(
    session: Session,
    business: Business,
    audit: WebsiteAudit,
    *,
    job_run_id: uuid.UUID | None,
    model: str,
    input_hash_value: str,
    status: ClassificationStatus,
    escalated: bool = False,
    output: dict[str, Any] | None = None,
    raw_output: str | None = None,
    rejected_claims: list[dict[str, Any]] | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    est_cost_usd: Decimal | None = None,
    latency_ms: int = 0,
    error: str | None = None,
    now: datetime | None = None,
) -> AIClassification:
    settings = get_settings()
    moment = now or datetime.now(UTC)
    ttl_days = settings.audit_content_ttl_days
    has_content = bool(raw_output) or bool(output and output.get("business_summary"))
    row = AIClassification(
        business_id=business.id,
        website_audit_id=audit.id,
        job_run_id=job_run_id,
        model=model or NO_MODEL,
        prompt_version=PROMPT_VERSION,
        input_hash=input_hash_value,
        status=status,
        escalated=escalated,
        output=output,
        raw_output=(raw_output or "")[: settings.ai_raw_output_max_chars] or None,
        rejected_claims=rejected_claims or [],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        est_cost_usd=est_cost_usd,
        latency_ms=latency_ms,
        error=error,
        content_expires_at=(
            moment + timedelta(days=ttl_days) if ttl_days > 0 and has_content else None
        ),
        # Stamped here rather than by the database: two rows written in one transaction
        # (triage, then escalation) must order the way they happened, and Postgres's
        # `now()` is the same for the whole transaction.
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


# --- merging ------------------------------------------------------------------------------


@dataclass
class MergedOpportunity:
    service: str
    source: OpportunitySource
    confidence: float
    reason: str
    evidence: list[dict[str, Any]]
    ai_agrees: bool | None


def merge(rules: list[RuleOpportunity], ai: AIOutput | None) -> list[MergedOpportunity]:
    """Rules first; the AI may confirm, raise a little, or add — never remove."""
    ai_by_service = {o.service: o for o in ai.opportunities} if ai is not None else {}
    merged: list[MergedOpportunity] = []
    for rule in rules:
        evidence = [{**e.as_dict(), "source": "rules"} for e in rule.evidence]
        suggestion = ai_by_service.pop(rule.service, None)
        if ai is None:
            merged.append(
                MergedOpportunity(
                    rule.service,
                    OpportunitySource.rules,
                    rule.confidence,
                    rule.reason,
                    evidence,
                    None,
                )
            )
            continue
        if suggestion is None:
            merged.append(
                MergedOpportunity(
                    rule.service,
                    OpportunitySource.rules,
                    rule.confidence,
                    rule.reason,
                    evidence,
                    False,
                )
            )
            continue
        raised = min(suggestion.confidence, rule.confidence + AI_MAX_RAISE)
        confidence = round(max(rule.confidence, raised), PLACES)
        reason = rule.reason
        if suggestion.rationale:
            reason = f"{reason} {suggestion.rationale}".strip()
        evidence.extend(_ai_evidence(suggestion))
        merged.append(
            MergedOpportunity(
                rule.service, OpportunitySource.rules_and_ai, confidence, reason, evidence, True
            )
        )

    for suggestion in ai_by_service.values():
        merged.append(
            MergedOpportunity(
                suggestion.service,
                OpportunitySource.ai,
                round(min(suggestion.confidence, AI_ONLY_MAX_CONFIDENCE), PLACES),
                suggestion.rationale or AI_ONLY_DEFAULT_REASON,
                _ai_evidence(suggestion),
                True,
            )
        )
    return merged


def _from_rule(
    rule: RuleOpportunity, evidence: list[dict[str, Any]], *, ai_agrees: bool | None
) -> MergedOpportunity:
    return MergedOpportunity(
        rule.service, OpportunitySource.rules, rule.confidence, rule.reason, evidence, ai_agrees
    )


def _ai_evidence(suggestion: Any) -> list[dict[str, Any]]:
    return [
        {
            "finding_code": item.finding_code,
            "text": item.quote,
            "url": item.source_url,
            "source": "ai",
        }
        for item in suggestion.evidence
    ]


# --- storing ------------------------------------------------------------------------------


# The statuses a reviewer may still act on. Re-classification refreshes these rows in
# place; a `needs_enrichment` row is exactly the one waiting for fresh evidence.
OPEN_STATUSES = (ReviewStatus.pending, ReviewStatus.needs_enrichment)
# Decisions that put a business+service into the cool-down: "not now" verdicts.
COOLDOWN_STATUSES = (ReviewStatus.rejected, ReviewStatus.not_a_fit, ReviewStatus.duplicate)


def pending_opportunities(session: Session, business_id: uuid.UUID) -> dict[str, Opportunity]:
    """The open (pending or needs_enrichment) opportunity per service, if any.

    A `pending` row wins over a `needs_enrichment` one for the same service, whatever
    their ages: `pending` is the status the unique index covers, so it is the row an
    insert would collide with and therefore the row to update.
    """
    rows = session.scalars(
        select(Opportunity)
        .where(
            Opportunity.business_id == business_id,
            Opportunity.review_status.in_(OPEN_STATUSES),
        )
        .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
    )
    found: dict[str, Opportunity] = {}
    for row in rows:
        seen = found.get(row.service)
        if seen is None or (
            seen.review_status is not ReviewStatus.pending
            and row.review_status is ReviewStatus.pending
        ):
            found[row.service] = row
    return found


def blocked_services(
    session: Session,
    business_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Services classification must not (re)create for this business, and why.

    `approved`: a human said yes; that row is theirs and is never overwritten, and no
    second pending row is opened beside it. `cooldown`: a human said no (rejected,
    not-a-fit, duplicate) within `REVIEW_COOLDOWN_DAYS`; asking again would be nagging.
    """
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=config.review_cooldown_days)
    blocked: dict[str, str] = {}
    rows = session.execute(
        select(Opportunity.service, Opportunity.review_status, Opportunity.decided_at).where(
            Opportunity.business_id == business_id,
            Opportunity.review_status.in_((ReviewStatus.approved, *COOLDOWN_STATUSES)),
        )
    ).all()
    for service_key, status, decided_at in rows:
        if status is ReviewStatus.approved:
            blocked[service_key] = "approved"
        elif decided_at is not None and decided_at >= since and service_key not in blocked:
            blocked[service_key] = "cooldown"
    return blocked


# The partial unique index that keeps one *pending* row per business and service.
PENDING_UNIQUE_INDEX = "uq_opportunities_pending_business_service"


def _apply_classification(
    row: Opportunity,
    *,
    item: MergedOpportunity,
    audit: WebsiteAudit,
    classification: AIClassification | None,
    computed: Score,
) -> None:
    """Write one classified service onto its opportunity row. The only writer of these fields."""
    row.website_audit_id = audit.id
    row.ai_classification_id = classification.id if classification is not None else None
    row.source = item.source
    row.reason = item.reason
    row.evidence = item.evidence
    row.confidence = Decimal(str(item.confidence))
    row.ai_agrees = item.ai_agrees
    row.score = Decimal(str(computed.total))
    row.score_components = computed.components()
    row.scoring_version = SCORING_VERSION


def open_opportunity_for(
    session: Session, business_id: uuid.UUID, service: str
) -> Opportunity | None:
    """Re-read the open row for one business and service, straight from the database."""
    return session.scalars(
        select(Opportunity)
        .where(
            Opportunity.business_id == business_id,
            Opportunity.service == service,
            Opportunity.review_status.in_(OPEN_STATUSES),
        )
        .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
    ).first()


def _open_row(
    session: Session,
    business_id: uuid.UUID,
    service: str,
    apply: Callable[[Opportunity], None],
) -> tuple[Opportunity, bool]:
    """The open row for this service, creating it if there is none. `(row, created)`.

    `apply` writes the classification onto whichever row we end up with, because the new
    row has to be complete before it is flushed and the row we might find instead has to
    be brought up to date afterwards.

    The insert goes in inside a savepoint of its own. `uq_opportunities_pending_business_service`
    is a partial unique index, so a row another writer committed between our read and our
    insert comes back as a `UniqueViolation` — and in the v0.9.0 incident that violation
    aborted the whole transaction and cost the business its entire classification. Here it
    means only that somebody else opened the row first: roll back to the savepoint, take
    theirs and update it. Idempotent either way, which is what re-classification is meant
    to be. Any *other* integrity error is a real fault and is re-raised.

    The flush before the savepoint is not tidiness: it pushes the rows updated earlier in
    this loop out first, so a rollback to the savepoint can only undo our own insert.
    """
    session.flush()
    savepoint = session.begin_nested()
    row = Opportunity(business_id=business_id, service=service, review_status=ReviewStatus.pending)
    apply(row)
    try:
        session.add(row)
        session.flush()
    except IntegrityError as exc:
        savepoint.rollback()
        if PENDING_UNIQUE_INDEX not in str(exc.orig):
            raise
    else:
        savepoint.commit()
        return row, True

    existing = open_opportunity_for(session, business_id, service)
    if existing is None:
        raise ConflictError(
            "An opportunity for this service could not be opened or found",
            details={"business_id": str(business_id), "service": service},
        )
    logger.info(
        "an opportunity for this service was opened by another writer; updating it",
        extra={"business_id": str(business_id), "service": service},
    )
    apply(existing)
    return existing, False


def upsert_opportunities(
    session: Session,
    business: Business,
    audit: WebsiteAudit,
    merged: list[MergedOpportunity],
    *,
    classification: AIClassification | None,
    intent_explicit: bool,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[list[Opportunity], int, int]:
    """One open row per service: update it if it exists, create it if not.

    A service a human already approved, or turned down within the cool-down, is skipped:
    the decided row is left exactly as the reviewer left it.
    """
    weights = Weights.from_settings(settings)
    existing = pending_opportunities(session, business.id)
    blocked = blocked_services(session, business.id, settings=settings, now=now)
    rows: list[Opportunity] = []
    created = updated = 0
    for item in merged:
        if item.service in blocked and item.service not in existing:
            logger.info(
                "service skipped by a review decision",
                extra={
                    "business_id": str(business.id),
                    "service": item.service,
                    "reason": blocked[item.service],
                },
            )
            continue
        computed = score(
            business,
            audit,
            confidence=item.confidence,
            intent_explicit=intent_explicit,
            weights=weights,
        )

        apply = partial(
            _apply_classification,
            item=item,
            audit=audit,
            classification=classification,
            computed=computed,
        )
        row = existing.get(item.service)
        if row is None:
            row, was_created = _open_row(session, business.id, item.service, apply)
            existing[item.service] = row
            created += 1 if was_created else 0
            updated += 0 if was_created else 1
        else:
            apply(row)
            updated += 1
        rows.append(row)
    session.flush()
    return rows, created, updated


# --- enqueueing ---------------------------------------------------------------------------


def enqueue_classification_for_run(
    session: Session,
    audit_run_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    dispatch: bool = True,
) -> JobRunType:
    """Queue a classification run for the businesses one audit run audited."""
    from app.modules.jobs.service import enqueue_run, get_job_run

    parent = get_job_run(session, audit_run_id)
    if parent.kind != AUDIT_JOB_KIND:
        raise ValidationFailedError(
            "Only an audit run's businesses can be classified",
            details={"job_run_id": str(audit_run_id), "kind": parent.kind},
        )
    return enqueue_run(
        session,
        search_job_id=parent.search_job_id,
        kind=CLASSIFICATION_JOB_KIND,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        dispatch=dispatch,
        params={"parent_run_id": str(parent.id)},
    )


def enqueue_classification_for_business(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> JobRunType:
    """Queue a re-classification of one business now."""
    from app.modules.businesses.service import get_business
    from app.modules.jobs.service import enqueue_run

    business = get_business(session, business_id)
    return enqueue_run(
        session,
        search_job_id=None,
        kind=CLASSIFICATION_JOB_KIND,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        params={"business_id": str(business.id)},
    )


def businesses_for_run(session: Session, audit_run_id: uuid.UUID) -> list[Business]:
    """The businesses one audit run audited, minus those closed for good."""
    run = session.get(JobRunType, audit_run_id)
    if run is None:
        raise NotFoundError("Job run not found", details={"job_run_id": str(audit_run_id)})
    business_ids = list(
        session.scalars(
            select(WebsiteAudit.business_id)
            .where(WebsiteAudit.job_run_id == audit_run_id)
            .distinct()
        )
    )
    if not business_ids:
        return []
    return list(
        session.scalars(
            select(Business)
            .where(
                Business.id.in_(business_ids),
                Business.business_status != BusinessStatus.closed_permanently,
            )
            .order_by(Business.created_at.asc(), Business.id.asc())
        )
    )


# --- reads --------------------------------------------------------------------------------


def get_opportunity(session: Session, opportunity_id: uuid.UUID) -> Opportunity:
    row = session.get(Opportunity, opportunity_id)
    if row is None:
        raise NotFoundError(
            "Opportunity not found", details={"opportunity_id": str(opportunity_id)}
        )
    return row


def list_opportunities(
    session: Session,
    *,
    service: str | None = None,
    review_status: ReviewStatus | None = ReviewStatus.pending,
    min_score: float | None = None,
    industry: str | None = None,
    city: str | None = None,
    state: str | None = None,
    source: OpportunitySource | None = None,
    business_id: uuid.UUID | None = None,
    sort: str = "score",
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[OpportunitySummary]:
    """Filtered list, best score first by default. Every filter is optional (AND)."""
    stmt: Any = select(Opportunity, Business).join(Business, Business.id == Opportunity.business_id)
    if service:
        stmt = stmt.where(Opportunity.service == service)
    if review_status is not None:
        stmt = stmt.where(Opportunity.review_status == review_status)
    if min_score is not None:
        stmt = stmt.where(Opportunity.score >= Decimal(str(min_score)))
    if industry:
        stmt = stmt.where(func.lower(Business.industry) == industry.lower())
    if city:
        stmt = stmt.where(func.lower(Business.city) == city.lower())
    if state:
        stmt = stmt.where(func.upper(Business.state) == state.upper())
    if source is not None:
        stmt = stmt.where(Opportunity.source == source)
    if business_id is not None:
        stmt = stmt.where(Opportunity.business_id == business_id)

    if sort == "created_at":
        stmt = stmt.order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
        stmt = apply_cursor(stmt, Opportunity.created_at, Opportunity.id, cursor)
    else:
        stmt = stmt.order_by(Opportunity.score.desc(), Opportunity.id.desc())
        stmt = _apply_score_cursor(stmt, cursor)
    rows = list(session.execute(stmt.limit(limit + 1)))

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1][0]
        next_cursor = (
            encode_cursor(last.created_at, last.id)
            if sort == "created_at"
            else _encode_score_cursor(last.score, last.id)
        )
    return Page[OpportunitySummary](
        items=[summarize(opportunity, business) for opportunity, business in rows],
        next_cursor=next_cursor,
    )


def _encode_score_cursor(value: Decimal, row_id: uuid.UUID) -> str:
    import base64

    return base64.urlsafe_b64encode(f"{value}|{row_id}".encode()).decode().rstrip("=")


def _apply_score_cursor(stmt: Any, cursor: str | None) -> Any:
    import base64
    import binascii

    if not cursor:
        return stmt
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw, _, id_raw = base64.urlsafe_b64decode(padded).decode().partition("|")
        value, row_id = Decimal(raw), uuid.UUID(id_raw)
    except (binascii.Error, UnicodeDecodeError, ValueError, ArithmeticError) as exc:
        raise ValidationFailedError("Cursor is not valid", details={"cursor": cursor}) from exc
    from sqlalchemy import literal, tuple_

    return stmt.where(
        tuple_(Opportunity.score, Opportunity.id) < tuple_(literal(value), literal(row_id))
    )


def summarize(opportunity: Opportunity, business: Business) -> OpportunitySummary:
    components = opportunity.score_components or {}
    return OpportunitySummary(
        id=opportunity.id,
        business_id=business.id,
        business_name=business.display_name,
        industry=business.industry,
        city=business.city,
        state=business.state,
        service=opportunity.service,
        source=opportunity.source,
        confidence=float(opportunity.confidence),
        ai_agrees=opportunity.ai_agrees,
        score=float(opportunity.score),
        score_components=ScoreComponentsRead(
            facts=float(components.get("facts", 0.0)),
            inference=float(components.get("inference", 0.0)),
            intent=float(components.get("intent", 0.0)),
            contactability=float(components.get("contactability", 0.0)),
        ),
        scoring_version=opportunity.scoring_version,
        review_status=opportunity.review_status,
        lock_version=opportunity.lock_version,
        top_evidence=opportunity.evidence[0] if opportunity.evidence else None,
        created_at=opportunity.created_at,
        updated_at=opportunity.updated_at,
    )


def detail(session: Session, opportunity: Opportunity) -> OpportunityDetail:
    business = session.get(Business, opportunity.business_id)
    assert business is not None  # the FK cascades: an opportunity cannot outlive its business
    provenance: AIProvenanceRead | None = None
    if opportunity.ai_classification_id is not None:
        row = session.get(AIClassification, opportunity.ai_classification_id)
        if row is not None:
            output = row.output or {}
            provenance = AIProvenanceRead(
                classification_id=row.id,
                model=row.model,
                prompt_version=row.prompt_version,
                status=row.status.value,
                escalated=row.escalated,
                buying_intent=output.get("buying_intent"),
                business_summary=output.get("business_summary"),
            )
    return OpportunityDetail(
        **summarize(opportunity, business).model_dump(),
        reason=opportunity.reason,
        evidence=list(opportunity.evidence or []),
        website_audit_id=opportunity.website_audit_id,
        ai_classification_id=opportunity.ai_classification_id,
        ai=provenance,
        assigned_to=opportunity.assigned_to,
        decided_at=opportunity.decided_at,
        decided_by=opportunity.decided_by,
    )


# --- retention ----------------------------------------------------------------------------


def purge_expired_ai_content(session: Session, *, now: datetime | None = None) -> int:
    """Null the raw model text and the business summary of classifications past their window.

    The validated opportunities, the evidence quotes and the rejected claims stay: they
    are the provenance of a claim a human may already be acting on.
    """
    moment = now or datetime.now(UTC)
    rows = list(
        session.scalars(
            select(AIClassification).where(
                AIClassification.content_expires_at.is_not(None),
                AIClassification.content_expires_at < moment,
                AIClassification.purged_at.is_(None),
            )
        )
    )
    for row in rows:
        row.raw_output = None
        if row.output and row.output.get("business_summary") is not None:
            row.output = {**row.output, "business_summary": None}
        row.purged_at = moment
    session.flush()
    if rows:
        logger.info("purged expired AI content", extra={"classifications": len(rows)})
    return len(rows)
