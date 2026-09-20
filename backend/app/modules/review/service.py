"""Human review (blueprint slide 41): decisions, undo, the queue, the detail and the leads.

Rules that hold everywhere in this module:

- only a `pending` or `needs_enrichment` opportunity can be decided, and only by someone
  whose `lock_version` matches — a stale one is a 409, not a silent overwrite;
- every decision writes a `review_decisions` row and an `audit_logs` row in the same
  transaction as the status change;
- an approval is one opportunity at a time, by one person; the batch endpoint accepts only
  reject and not-a-fit;
- do-not-contact is business-wide: every opportunity of the business is closed, and a
  suppression is added so the business never gets a pending opportunity again;
- an undo within the window restores exactly what the decision changed, and nothing else.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page
from app.modules.ai.models import AIClassification
from app.modules.audit import service as audit_log
from app.modules.audit_web import service as audits
from app.modules.audit_web.models import WebsiteAudit
from app.modules.auth.models import Role, User
from app.modules.businesses import service as businesses
from app.modules.businesses.models import Business
from app.modules.compliance import service as compliance
from app.modules.compliance.models import SuppressionSource
from app.modules.opportunities import service as opportunities
from app.modules.opportunities.catalogue import SERVICES
from app.modules.opportunities.models import Opportunity, ReviewStatus
from app.modules.review.models import Decision, ReviewDecision
from app.modules.review.schemas import (
    NOT_A_FIT_REASON_CODES,
    REJECT_REASON_CODES,
    AISummaryRead,
    BatchItemResult,
    BatchReviewRequest,
    BatchReviewResult,
    DecidedOpportunity,
    LeadRead,
    QueueAudit,
    QueueItem,
    QueueOpportunity,
    ReviewDecisionRead,
    ReviewDetail,
    ReviewOpportunity,
    ReviewRequest,
    UndoResult,
)

logger = get_logger("app.review")

DECIDABLE = frozenset({ReviewStatus.pending, ReviewStatus.needs_enrichment})
TO_STATUS: dict[Decision, ReviewStatus] = {
    Decision.approve: ReviewStatus.approved,
    Decision.reject: ReviewStatus.rejected,
    Decision.needs_enrichment: ReviewStatus.needs_enrichment,
    Decision.duplicate: ReviewStatus.duplicate,
    Decision.not_a_fit: ReviewStatus.not_a_fit,
    Decision.do_not_contact: ReviewStatus.do_not_contact,
}
REASON_CODES: dict[Decision, tuple[str, ...]] = {
    Decision.reject: REJECT_REASON_CODES,
    Decision.not_a_fit: NOT_A_FIT_REASON_CODES,
}
QUEUE_STATUSES = frozenset({ReviewStatus.pending, ReviewStatus.needs_enrichment})
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
TOP_FINDINGS = 3
STALE_LOCK_MESSAGE = "Another reviewer already decided this"


# --- deciding -----------------------------------------------------------------------------


def decide(
    session: Session,
    opportunity_id: uuid.UUID,
    payload: ReviewRequest,
    *,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> DecidedOpportunity:
    """Apply one decision to one opportunity. Raises rather than guesses on any problem."""
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    opportunity = opportunities.get_opportunity(session, opportunity_id)
    # The lock first: a reviewer holding a stale version is told someone else got there,
    # whatever state the row is in now. The state check catches a current-version request
    # on a row that is simply not open any more.
    _check_lock(opportunity, payload.lock_version)
    _check_decidable(opportunity)
    fields = _validate(session, opportunity, payload)

    if payload.decision is Decision.do_not_contact:
        decision = _do_not_contact(
            session, opportunity, note=fields["note"], actor=actor, now=moment
        )
    else:
        decision = _apply(
            session,
            opportunity,
            decision=payload.decision,
            to_status=TO_STATUS[payload.decision],
            actor=actor,
            now=moment,
            reason_code=fields["reason_code"],
            note=fields["note"],
            duplicate_of=fields["duplicate_of"],
            assigned_to=fields["assigned_to"],
        )
    session.flush()
    return DecidedOpportunity(
        **opportunities.detail(session, opportunity).model_dump(),
        decision=read_decision(
            session, decision, actor=actor, now=moment, settings=config, current=opportunity
        ),
    )


def _check_decidable(opportunity: Opportunity) -> None:
    if opportunity.review_status not in DECIDABLE:
        raise InvalidStateTransitionError(
            f"An opportunity with status '{opportunity.review_status.value}' cannot be decided",
            details={
                "opportunity_id": str(opportunity.id),
                "review_status": opportunity.review_status.value,
                "lock_version": opportunity.lock_version,
            },
        )


def _check_lock(opportunity: Opportunity, lock_version: int) -> None:
    if opportunity.lock_version != lock_version:
        raise ConflictError(
            STALE_LOCK_MESSAGE,
            code="stale_lock_version",
            details={
                "opportunity_id": str(opportunity.id),
                "lock_version": opportunity.lock_version,
                "sent": lock_version,
                "review_status": opportunity.review_status.value,
            },
        )


def _validate(session: Session, opportunity: Opportunity, payload: ReviewRequest) -> dict[str, Any]:
    """The required fields per decision (spec table). Anything missing is a 422."""
    note = (payload.note or "").strip() or None
    reason_code = (payload.reason_code or "").strip() or None
    fields: dict[str, Any] = {
        "note": note,
        "reason_code": None,
        "duplicate_of": None,
        "assigned_to": None,
    }
    decision = payload.decision

    if decision in REASON_CODES:
        allowed = REASON_CODES[decision]
        if reason_code not in allowed:
            raise ValidationFailedError(
                f"A {decision.value} decision needs a reason_code",
                details={"field": "reason_code", "allowed": list(allowed)},
            )
        if reason_code == "other" and note is None:
            raise ValidationFailedError(
                "A note is required when the reason is 'other'", details={"field": "note"}
            )
        fields["reason_code"] = reason_code
    elif decision in (Decision.needs_enrichment, Decision.do_not_contact) and note is None:
        raise ValidationFailedError(
            f"A {decision.value} decision needs a note", details={"field": "note"}
        )

    if decision is Decision.duplicate:
        fields["duplicate_of"] = _validate_duplicate_of(session, opportunity, payload.duplicate_of)
    if decision is Decision.approve and payload.assigned_to is not None:
        fields["assigned_to"] = _validate_assignee(session, payload.assigned_to)
    return fields


def _validate_duplicate_of(
    session: Session, opportunity: Opportunity, duplicate_of: uuid.UUID | None
) -> uuid.UUID:
    if duplicate_of is None:
        raise ValidationFailedError(
            "A duplicate decision needs duplicate_of", details={"field": "duplicate_of"}
        )
    if duplicate_of == opportunity.id:
        raise ValidationFailedError(
            "An opportunity cannot be a duplicate of itself", details={"field": "duplicate_of"}
        )
    other = session.get(Opportunity, duplicate_of)
    if other is None:
        raise ValidationFailedError(
            "duplicate_of does not name an opportunity",
            details={"field": "duplicate_of", "duplicate_of": str(duplicate_of)},
        )
    if other.service != opportunity.service:
        raise ValidationFailedError(
            "duplicate_of must be an opportunity for the same service",
            details={
                "field": "duplicate_of",
                "service": opportunity.service,
                "duplicate_of_service": other.service,
            },
        )
    return other.id


def _validate_assignee(session: Session, user_id: uuid.UUID) -> uuid.UUID:
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.role is not Role.sales_rep:
        raise ValidationFailedError(
            "assigned_to must be an active user with the sales_rep role",
            details={"field": "assigned_to", "assigned_to": str(user_id)},
        )
    return user.id


def _apply(
    session: Session,
    opportunity: Opportunity,
    *,
    decision: Decision,
    to_status: ReviewStatus,
    actor: User,
    now: datetime,
    reason_code: str | None = None,
    note: str | None = None,
    duplicate_of: uuid.UUID | None = None,
    assigned_to: uuid.UUID | None = None,
) -> ReviewDecision:
    """Change the status, bump the lock, write the history row and the audit row."""
    from_status = opportunity.review_status
    before = _snapshot(opportunity)
    row = ReviewDecision(
        opportunity_id=opportunity.id,
        decision=decision,
        from_status=from_status,
        to_status=to_status,
        reason_code=reason_code,
        note=note,
        duplicate_of=duplicate_of,
        assigned_to=assigned_to,
        decided_by=actor.id,
        decided_at=now,
    )
    session.add(row)
    opportunity.review_status = to_status
    opportunity.decided_at = now
    opportunity.decided_by = actor.id
    opportunity.lock_version += 1
    if decision is Decision.approve:
        opportunity.assigned_to = assigned_to
    session.flush()
    audit_log.record(
        session,
        action="opportunity.reviewed",
        entity_type="opportunity",
        entity_id=opportunity.id,
        actor_id=actor.id,
        before=before,
        after={
            **_snapshot(opportunity),
            "decision": decision.value,
            "decision_id": str(row.id),
            "reason_code": reason_code,
            "note": note,
            "duplicate_of": str(duplicate_of) if duplicate_of else None,
        },
    )
    logger.info(
        "opportunity reviewed",
        extra={
            "opportunity_id": str(opportunity.id),
            "decision": decision.value,
            "from_status": from_status.value,
            "to_status": to_status.value,
        },
    )
    return row


def _do_not_contact(
    session: Session, target: Opportunity, *, note: str | None, actor: User, now: datetime
) -> ReviewDecision:
    """Close every opportunity of the business and suppress it. Returns the target's row."""
    business = businesses.get_business(session, target.business_id)
    rows = list(
        session.scalars(
            select(Opportunity)
            .where(
                Opportunity.business_id == business.id,
                Opportunity.review_status != ReviewStatus.do_not_contact,
            )
            .order_by(Opportunity.created_at.asc(), Opportunity.id.asc())
        )
    )
    written: ReviewDecision | None = None
    for row in rows:
        decision = _apply(
            session,
            row,
            decision=Decision.do_not_contact,
            to_status=ReviewStatus.do_not_contact,
            actor=actor,
            now=now,
            note=note,
        )
        if row.id == target.id:
            written = decision
    assert written is not None  # the target is one of the business's opportunities
    compliance.suppress_business(
        session,
        business,
        reason=note or "do not contact",
        source=SuppressionSource.review,
        actor_id=actor.id,
        now=now,
    )
    return written


def _snapshot(opportunity: Opportunity) -> dict[str, Any]:
    return {
        "review_status": opportunity.review_status.value,
        "lock_version": opportunity.lock_version,
        "assigned_to": str(opportunity.assigned_to) if opportunity.assigned_to else None,
        "decided_by": str(opportunity.decided_by) if opportunity.decided_by else None,
        "decided_at": opportunity.decided_at.isoformat() if opportunity.decided_at else None,
    }


# --- batch --------------------------------------------------------------------------------


def decide_batch(
    session: Session,
    payload: BatchReviewRequest,
    *,
    actor: User,
    now: datetime | None = None,
) -> BatchReviewResult:
    """Reject or not-a-fit, up to 50 at once. Each id gets its own verdict; none is skipped."""
    moment = now or datetime.now(UTC)
    decision = Decision(payload.decision)
    allowed = REASON_CODES[decision]
    reason_code = payload.reason_code.strip()
    note = (payload.note or "").strip() or None
    if reason_code not in allowed:
        raise ValidationFailedError(
            f"A {decision.value} decision needs a reason_code",
            details={"field": "reason_code", "allowed": list(allowed)},
        )
    if reason_code == "other" and note is None:
        raise ValidationFailedError(
            "A note is required when the reason is 'other'", details={"field": "note"}
        )

    items: list[BatchItemResult] = []
    decided = 0
    seen: set[uuid.UUID] = set()
    for opportunity_id in payload.ids:
        if opportunity_id in seen:
            continue
        seen.add(opportunity_id)
        row = session.get(Opportunity, opportunity_id)
        if row is None:
            items.append(
                BatchItemResult(
                    id=opportunity_id,
                    result="not_allowed",
                    review_status=None,
                    lock_version=None,
                    message="Opportunity not found",
                )
            )
            continue
        if row.review_status not in DECIDABLE:
            items.append(
                BatchItemResult(
                    id=row.id,
                    result="conflict",
                    review_status=row.review_status,
                    lock_version=row.lock_version,
                    message=STALE_LOCK_MESSAGE,
                )
            )
            continue
        _apply(
            session,
            row,
            decision=decision,
            to_status=TO_STATUS[decision],
            actor=actor,
            now=moment,
            reason_code=reason_code,
            note=note,
        )
        decided += 1
        items.append(
            BatchItemResult(
                id=row.id,
                result="ok",
                review_status=row.review_status,
                lock_version=row.lock_version,
                message=None,
            )
        )
    session.flush()
    return BatchReviewResult(decided=decided, items=items)


# --- undo ---------------------------------------------------------------------------------


def undo(
    session: Session,
    decision_id: uuid.UUID,
    *,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> UndoResult:
    """Put the opportunity back the way it was before this decision, within the window."""
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    row = get_decision(session, decision_id)
    if row.undone_at is not None:
        raise ConflictError(
            "This decision was already undone",
            code="already_undone",
            details={"decision_id": str(row.id), "undone_at": row.undone_at.isoformat()},
        )
    if actor.role is not Role.admin and row.decided_by != actor.id:
        raise PermissionDeniedError(
            "Only the reviewer who made a decision, or an admin, may undo it",
            details={"decision_id": str(row.id)},
        )
    deadline = undo_deadline(row, config)
    if moment > deadline:
        raise ConflictError(
            "The undo window for this decision has closed",
            code="undo_window_closed",
            details={"decision_id": str(row.id), "undo_until": deadline.isoformat()},
        )

    rows = [row]
    if row.decision is Decision.do_not_contact:
        rows = _do_not_contact_siblings(session, row)

    undone: list[ReviewDecision] = []
    restored: list[Opportunity] = []
    for item in rows:
        opportunity = opportunities.get_opportunity(session, item.opportunity_id)
        if opportunity.review_status is not item.to_status:
            raise ConflictError(
                "The opportunity has changed since this decision; it cannot be undone",
                code="undo_conflict",
                details={
                    "decision_id": str(item.id),
                    "opportunity_id": str(opportunity.id),
                    "review_status": opportunity.review_status.value,
                },
            )
        _restore(session, opportunity, item, actor=actor, now=moment)
        undone.append(item)
        restored.append(opportunity)

    lifted = 0
    if row.decision is Decision.do_not_contact:
        business_id = restored[0].business_id
        lifted = compliance.lift_review_suppressions(session, business_id, actor_id=actor.id)
    session.flush()
    return UndoResult(
        undone=[
            read_decision(session, item, actor=actor, now=moment, settings=config)
            for item in undone
        ],
        opportunities=[opportunities.detail(session, item) for item in restored],
        suppressions_lifted=lifted,
    )


def _do_not_contact_siblings(session: Session, row: ReviewDecision) -> list[ReviewDecision]:
    """The rows one do-not-contact wrote: same business, same person, same instant."""
    target = opportunities.get_opportunity(session, row.opportunity_id)
    return list(
        session.scalars(
            select(ReviewDecision)
            .join(Opportunity, Opportunity.id == ReviewDecision.opportunity_id)
            .where(
                Opportunity.business_id == target.business_id,
                ReviewDecision.decision == Decision.do_not_contact,
                ReviewDecision.decided_by == row.decided_by,
                ReviewDecision.decided_at == row.decided_at,
                ReviewDecision.undone_at.is_(None),
            )
            .order_by(ReviewDecision.id)
        )
    )


def _restore(
    session: Session, opportunity: Opportunity, row: ReviewDecision, *, actor: User, now: datetime
) -> None:
    before = _snapshot(opportunity)
    previous = _previous_decision(session, row)
    opportunity.review_status = row.from_status
    opportunity.lock_version += 1
    if previous is not None and row.from_status not in DECIDABLE:
        opportunity.decided_at = previous.decided_at
        opportunity.decided_by = previous.decided_by
    else:
        opportunity.decided_at = None
        opportunity.decided_by = None
    if row.decision is Decision.approve:
        opportunity.assigned_to = None
    row.undone_at = now
    row.undone_by = actor.id
    session.flush()
    audit_log.record(
        session,
        action="opportunity.review_undone",
        entity_type="opportunity",
        entity_id=opportunity.id,
        actor_id=actor.id,
        before=before,
        after={
            **_snapshot(opportunity),
            "decision_id": str(row.id),
            "decision": row.decision.value,
        },
    )
    logger.info(
        "review decision undone",
        extra={"opportunity_id": str(opportunity.id), "decision_id": str(row.id)},
    )


def _previous_decision(session: Session, row: ReviewDecision) -> ReviewDecision | None:
    """The decision that was in force before `row`, so its stamp can be put back."""
    return session.scalars(
        select(ReviewDecision)
        .where(
            ReviewDecision.opportunity_id == row.opportunity_id,
            ReviewDecision.undone_at.is_(None),
            ReviewDecision.id != row.id,
            ReviewDecision.decided_at <= row.decided_at,
        )
        .order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id.desc())
        .limit(1)
    ).first()


def undo_deadline(row: ReviewDecision, settings: Settings) -> datetime:
    return row.decided_at + timedelta(minutes=settings.review_undo_window_minutes)


def get_decision(session: Session, decision_id: uuid.UUID) -> ReviewDecision:
    row = session.get(ReviewDecision, decision_id)
    if row is None:
        raise NotFoundError("Review decision not found", details={"decision_id": str(decision_id)})
    return row


def read_decision(
    session: Session,
    row: ReviewDecision,
    *,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
    current: Opportunity | None = None,
    emails: dict[uuid.UUID, str] | None = None,
) -> ReviewDecisionRead:
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    deadline = undo_deadline(row, config)
    opportunity = current or session.get(Opportunity, row.opportunity_id)
    in_force = opportunity is not None and opportunity.review_status is row.to_status
    can_undo = (
        row.undone_at is None
        and in_force
        and moment <= deadline
        and (actor.role is Role.admin or row.decided_by == actor.id)
    )
    lookup = emails if emails is not None else _emails(session, [row.decided_by, row.assigned_to])
    return ReviewDecisionRead(
        id=row.id,
        opportunity_id=row.opportunity_id,
        decision=row.decision,
        from_status=row.from_status,
        to_status=row.to_status,
        reason_code=row.reason_code,
        note=row.note,
        duplicate_of=row.duplicate_of,
        assigned_to=row.assigned_to,
        assigned_to_email=lookup.get(row.assigned_to) if row.assigned_to else None,
        decided_by=row.decided_by,
        decided_by_email=lookup.get(row.decided_by),
        decided_at=row.decided_at,
        undone_at=row.undone_at,
        undone_by=row.undone_by,
        undo_until=deadline,
        can_undo=can_undo,
    )


def _emails(session: Session, ids: Sequence[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = session.execute(select(User.id, User.email).where(User.id.in_(wanted))).all()
    return {row.id: row.email for row in rows}


# --- queue --------------------------------------------------------------------------------


def review_queue(
    session: Session,
    *,
    status: ReviewStatus = ReviewStatus.pending,
    service: str | None = None,
    city: str | None = None,
    state: str | None = None,
    industry: str | None = None,
    min_score: float | None = None,
    include_weak: bool = False,
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    settings: Settings | None = None,
) -> Page[QueueItem]:
    """Businesses with at least one open opportunity, strongest first, suppressed ones out."""
    config = settings or get_settings()
    if status not in QUEUE_STATUSES:
        raise ValidationFailedError(
            "The queue lists pending or needs_enrichment opportunities",
            details={"status": status.value},
        )
    weak = Decimal(str(config.review_weak_confidence))
    strong = Opportunity.confidence >= weak

    # One row per business: its best score among the shown opportunities and how many
    # were hidden for being weak.
    top_score = (
        func.max(Opportunity.score) if include_weak else func.max(Opportunity.score).filter(strong)
    )
    hidden = literal(0) if include_weak else func.count(Opportunity.id).filter(~strong)
    grouped = (
        select(
            Opportunity.business_id.label("business_id"),
            top_score.label("top_score"),
            hidden.label("weak_hidden"),
        )
        .where(Opportunity.review_status == status)
        .group_by(Opportunity.business_id)
    )
    if service:
        grouped = grouped.having(func.bool_or(Opportunity.service == service))
    agg = grouped.subquery()

    stmt: Select[Any] = (
        select(Business, agg.c.top_score, agg.c.weak_hidden)
        .join(agg, agg.c.business_id == Business.id)
        .where(agg.c.top_score.is_not(None), compliance.not_suppressed())
    )
    if city:
        stmt = stmt.where(func.lower(Business.city) == city.lower())
    if state:
        stmt = stmt.where(func.upper(Business.state) == state.upper())
    if industry:
        stmt = stmt.where(func.lower(Business.industry) == industry.lower())
    if min_score is not None:
        stmt = stmt.where(agg.c.top_score >= Decimal(str(min_score)))
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Business.display_name).like(pattern),
                func.lower(func.coalesce(Business.domain, "")).like(pattern),
            )
        )
    if cursor:
        score_after, id_after = _decode_queue_cursor(cursor)
        stmt = stmt.where(
            tuple_(agg.c.top_score, Business.id) < tuple_(literal(score_after), literal(id_after))
        )
    stmt = stmt.order_by(agg.c.top_score.desc(), Business.id.desc()).limit(limit + 1)
    rows = list(session.execute(stmt))

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last_business, last_score, _ = rows[-1]
        next_cursor = _encode_queue_cursor(Decimal(last_score), last_business.id)

    business_ids = [row[0].id for row in rows]
    open_rows = _open_opportunities(session, business_ids, status)
    latest = audits.latest_audits(session, business_ids)
    items = [
        _queue_item(
            business,
            float(score),
            int(weak_hidden),
            open_rows.get(business.id, []),
            latest.get(business.id),
            weak=weak,
            include_weak=include_weak,
        )
        for business, score, weak_hidden in rows
    ]
    return Page[QueueItem](items=items, next_cursor=next_cursor)


def _open_opportunities(
    session: Session, business_ids: list[uuid.UUID], status: ReviewStatus
) -> dict[uuid.UUID, list[Opportunity]]:
    if not business_ids:
        return {}
    grouped: dict[uuid.UUID, list[Opportunity]] = defaultdict(list)
    for row in session.scalars(
        select(Opportunity)
        .where(Opportunity.business_id.in_(business_ids), Opportunity.review_status == status)
        .order_by(Opportunity.score.desc(), Opportunity.id.desc())
    ):
        grouped[row.business_id].append(row)
    return grouped


def _queue_item(
    business: Business,
    top_score: float,
    weak_hidden: int,
    rows: list[Opportunity],
    latest: WebsiteAudit | None,
    *,
    weak: Decimal,
    include_weak: bool,
) -> QueueItem:
    shown = [row for row in rows if include_weak or row.confidence >= weak]
    return QueueItem(
        business_id=business.id,
        display_name=business.display_name,
        city=business.city,
        state=business.state,
        industry=business.industry,
        website=business.website,
        top_score=top_score,
        latest_audit=(
            QueueAudit(
                status=latest.status,
                audited_at=latest.created_at,
                top_findings=top_findings(latest),
            )
            if latest is not None
            else None
        ),
        opportunities=[
            QueueOpportunity(
                id=row.id,
                service=row.service,
                service_name=service_name(row.service),
                source=row.source,
                confidence=float(row.confidence),
                score=float(row.score),
                review_status=row.review_status,
                lock_version=row.lock_version,
                reason=row.reason,
                weak=row.confidence < weak,
            )
            for row in shown
        ],
        weak_hidden=weak_hidden,
    )


def top_findings(audit: WebsiteAudit) -> list[str]:
    """The worst few finding codes of an audit, high severity first."""
    ranked = sorted(
        (item for item in (audit.findings or []) if item.get("code")),
        key=lambda item: SEVERITY_ORDER.get(str(item.get("severity")), len(SEVERITY_ORDER)),
    )
    return [str(item["code"]) for item in ranked[:TOP_FINDINGS]]


def service_name(key: str) -> str:
    spec = SERVICES.get(key)
    return spec.name if spec is not None else key


def _encode_queue_cursor(score: Decimal, business_id: uuid.UUID) -> str:
    import base64

    return base64.urlsafe_b64encode(f"{score}|{business_id}".encode()).decode().rstrip("=")


def _decode_queue_cursor(cursor: str) -> tuple[Decimal, uuid.UUID]:
    import base64
    import binascii

    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw, _, id_raw = base64.urlsafe_b64decode(padded).decode().partition("|")
        return Decimal(raw), uuid.UUID(id_raw)
    except (binascii.Error, UnicodeDecodeError, ValueError, ArithmeticError) as exc:
        raise ValidationFailedError("Cursor is not valid", details={"cursor": cursor}) from exc


# --- detail -------------------------------------------------------------------------------


def review_detail(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> ReviewDetail:
    """One call with everything: facts + provenance, audit, AI summary, opportunities, history."""
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    business = businesses.get_business(session, business_id)
    latest = audits.latest_audit(session, business.id)
    rows = list(
        session.scalars(
            select(Opportunity)
            .where(Opportunity.business_id == business.id)
            .order_by(
                Opportunity.score.desc(), Opportunity.created_at.desc(), Opportunity.id.desc()
            )
        )
    )
    history = _history(session, [row.id for row in rows])
    people = _emails(
        session,
        [d.decided_by for rows_ in history.values() for d in rows_]
        + [d.assigned_to for rows_ in history.values() for d in rows_],
    )
    weak = Decimal(str(config.review_weak_confidence))
    active = compliance.active_suppressions_for(session, business)
    return ReviewDetail(
        business=businesses.detail(session, business),
        audit=audits.detail(latest, include_page_text=False) if latest is not None else None,
        ai=_ai_summary(session, business.id),
        opportunities=[
            ReviewOpportunity(
                **opportunities.detail(session, row).model_dump(),
                service_name=service_name(row.service),
                history=[
                    read_decision(
                        session,
                        item,
                        actor=actor,
                        now=moment,
                        settings=config,
                        current=row,
                        emails=people,
                    )
                    for item in history.get(row.id, [])
                ],
                weak=row.confidence < weak,
            )
            for row in rows
        ],
        suppressed=bool(active),
        suppressions=[compliance.read(item, business.display_name) for item in active],
        undo_window_minutes=config.review_undo_window_minutes,
        weak_confidence=float(weak),
    )


def _history(
    session: Session, opportunity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ReviewDecision]]:
    if not opportunity_ids:
        return {}
    grouped: dict[uuid.UUID, list[ReviewDecision]] = defaultdict(list)
    for row in session.scalars(
        select(ReviewDecision)
        .where(ReviewDecision.opportunity_id.in_(opportunity_ids))
        .order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id.desc())
    ):
        grouped[row.opportunity_id].append(row)
    return grouped


def _ai_summary(session: Session, business_id: uuid.UUID) -> AISummaryRead | None:
    row = session.scalars(
        select(AIClassification)
        .where(AIClassification.business_id == business_id, AIClassification.output.is_not(None))
        .order_by(AIClassification.created_at.desc(), AIClassification.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    output = row.output or {}
    unknowns = output.get("unknowns") or []
    return AISummaryRead(
        classification_id=row.id,
        model=row.model,
        prompt_version=row.prompt_version,
        status=row.status.value,
        escalated=row.escalated,
        business_summary=output.get("business_summary"),
        industry=output.get("industry"),
        industry_matches_listing=output.get("industry_matches_listing"),
        buying_intent=output.get("buying_intent"),
        unknowns=[str(item) for item in unknowns],
        created_at=row.created_at,
    )


# --- leads --------------------------------------------------------------------------------


def list_leads(
    session: Session,
    *,
    actor: User,
    service: str | None = None,
    assigned_to: uuid.UUID | None = None,
    city: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[LeadRead]:
    """Approved and not suppressed. A sales rep only ever sees what is assigned to them."""
    stmt: Select[Any] = (
        select(Opportunity, Business)
        .join(Business, Business.id == Opportunity.business_id)
        .where(Opportunity.review_status == ReviewStatus.approved, compliance.not_suppressed())
    )
    if actor.role is Role.sales_rep:
        stmt = stmt.where(Opportunity.assigned_to == actor.id)
    elif assigned_to is not None:
        stmt = stmt.where(Opportunity.assigned_to == assigned_to)
    if service:
        stmt = stmt.where(Opportunity.service == service)
    if city:
        stmt = stmt.where(func.lower(Business.city) == city.lower())
    if cursor:
        after_at, after_id = _decode_lead_cursor(cursor)
        stmt = stmt.where(
            tuple_(Opportunity.decided_at, Opportunity.id)
            < tuple_(literal(after_at), literal(after_id))
        )
    stmt = stmt.order_by(Opportunity.decided_at.desc(), Opportunity.id.desc()).limit(limit + 1)
    rows = list(session.execute(stmt))

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1][0]
        assert last.decided_at is not None  # approved rows always carry their stamp
        next_cursor = _encode_lead_cursor(last.decided_at, last.id)

    people = _emails(session, [o.decided_by for o, _ in rows] + [o.assigned_to for o, _ in rows])
    return Page[LeadRead](
        items=[_lead(opportunity, business, people) for opportunity, business in rows],
        next_cursor=next_cursor,
    )


def _lead(opportunity: Opportunity, business: Business, people: dict[uuid.UUID, str]) -> LeadRead:
    return LeadRead(
        opportunity_id=opportunity.id,
        business_id=business.id,
        business_name=business.display_name,
        city=business.city,
        state=business.state,
        industry=business.industry,
        service=opportunity.service,
        service_name=service_name(opportunity.service),
        score=float(opportunity.score),
        reason=opportunity.reason,
        approved_by=opportunity.decided_by,
        approved_by_email=people.get(opportunity.decided_by) if opportunity.decided_by else None,
        approved_at=opportunity.decided_at,
        assigned_to=opportunity.assigned_to,
        assigned_to_email=people.get(opportunity.assigned_to) if opportunity.assigned_to else None,
        phone_e164=business.phone_e164,
        website=business.website,
        lock_version=opportunity.lock_version,
        top_evidence=opportunity.evidence[0] if opportunity.evidence else None,
    )


def _encode_lead_cursor(decided_at: datetime, row_id: uuid.UUID) -> str:
    from app.core.pagination import encode_cursor

    return encode_cursor(decided_at, row_id)


def _decode_lead_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    from app.core.pagination import decode_cursor

    position = decode_cursor(cursor)
    return position.created_at, position.id


__all__ = [
    "decide",
    "decide_batch",
    "list_leads",
    "review_detail",
    "review_queue",
    "undo",
]
