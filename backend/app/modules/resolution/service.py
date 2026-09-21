"""Resolution against the database: one record, one run, and a reviewer's decision.

The invariants this file is responsible for:

* a record is linked to at most one business, and only ever by a decision it can explain;
* a mid-confidence pair creates no business at all until a human has looked at it;
* running the same resolution twice changes nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.modules.adapters import registry
from app.modules.adapters.base import Candidate, RawDoc
from app.modules.adapters.registry import UnknownAdapterError
from app.modules.audit import service as audit
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord, RecordSighting
from app.modules.jobs.models import JobRun
from app.modules.normalization.normalize import NormalizationError, normalize
from app.modules.normalization.schemas import NormalizedBusiness
from app.modules.resolution import survivorship
from app.modules.resolution.blocking import find_candidates
from app.modules.resolution.models import MatchCandidate, MatchCandidateStatus, ResolutionStatus
from app.modules.resolution.resolver import Decision, DecisionKind, ScoredCandidate, decide
from app.modules.resolution.schemas import MatchCandidateDetail, ResolutionResultSummary
from app.modules.sources.models import Source

logger = get_logger("app.resolution")

PURGED_PAYLOAD_ERROR = "The stored payload has expired and been purged; nothing left to normalize"
# Imported by name rather than from `jobs.service`, which imports this module back.
DISCOVERY_JOB_KIND = "discovery"


@dataclass(frozen=True)
class RecordOutcome:
    """What happened to one record, for the run summary and for the logs."""

    kind: DecisionKind | None
    business_id: uuid.UUID | None = None
    created: bool = False
    reused: bool = False
    invalid_reason: str | None = None


# --- enqueueing -----------------------------------------------------------------------


def enqueue_resolution(
    session: Session,
    discovery_run_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    dispatch: bool = True,
) -> JobRun:
    """Queue a resolution run for one discovery run.

    Only a discovery run can be resolved; asking for anything else is a 422 rather than a
    run that would quietly find nothing.
    """
    from app.modules.jobs.service import RESOLUTION_JOB_KIND, enqueue_run, get_job_run

    parent = get_job_run(session, discovery_run_id)
    if parent.kind != DISCOVERY_JOB_KIND:
        raise ValidationFailedError(
            "Only a discovery run can be resolved",
            details={"job_run_id": str(discovery_run_id), "kind": parent.kind},
        )
    return enqueue_run(
        session,
        search_job_id=parent.search_job_id,
        kind=RESOLUTION_JOB_KIND,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        dispatch=dispatch,
        params={"parent_run_id": str(parent.id)},
    )


# --- one record ---------------------------------------------------------------------


def resolve_record(
    session: Session, record: DiscoveredRecord, *, now: datetime | None = None
) -> RecordOutcome:
    """Normalize one record and link, queue or create, per the resolver's decision."""
    moment = now or datetime.now(UTC)

    try:
        normalized = _normalize_record(session, record)
    except NormalizationError as exc:
        return _mark_invalid(session, record, "; ".join(exc.errors), now=moment)

    # Rule 0: this record already belongs to a business. Refresh what it says, never
    # score it again — that is also what makes a second resolution run a no-op.
    if record.business_id is not None:
        business = session.get(Business, record.business_id)
        if business is not None:
            _attach(session, record=record, business=business, normalized=normalized, now=moment)
            return RecordOutcome(kind=DecisionKind.auto_merge, business_id=business.id, reused=True)
        record.business_id = None

    decision = decide(normalized, find_candidates(session, normalized))

    if decision.kind is DecisionKind.auto_merge and decision.best is not None:
        business = _lock(session, decision.best.business)
        _attach(session, record=record, business=business, normalized=normalized, now=moment)
        _resolve_siblings(session, record, keep=business.id, now=moment)
        logger.info(
            "record auto-merged",
            extra={
                "discovered_record_id": str(record.id),
                "business_id": str(business.id),
                "score": decision.best.match.score,
            },
        )
        return RecordOutcome(kind=DecisionKind.auto_merge, business_id=business.id)

    if decision.kind is DecisionKind.review:
        _queue_for_review(session, record, decision, now=moment)
        return RecordOutcome(kind=DecisionKind.review)

    business = _create_business(session, normalized)
    _attach(session, record=record, business=business, normalized=normalized, now=moment)
    return RecordOutcome(kind=DecisionKind.new, business_id=business.id, created=True)


def _normalize_record(session: Session, record: DiscoveredRecord) -> NormalizedBusiness:
    source_name = session.scalar(select(Source.name).where(Source.id == record.source_id)) or ""
    if not record.raw_payload:
        raise NormalizationError(PURGED_PAYLOAD_ERROR)
    try:
        adapter = registry.get(source_name)
    except UnknownAdapterError as exc:
        raise NormalizationError(f"No adapter is registered for source '{source_name}'") from exc

    raw = RawDoc(
        source=source_name,
        source_record_id=record.source_record_id,
        source_url=record.source_url,
        payload=record.raw_payload,
        fetched_at=record.last_discovered_at,
    )
    if not adapter.validate(raw).valid:
        raise NormalizationError("The stored payload no longer validates against its adapter")
    candidate: Candidate = adapter.normalize(raw)
    return normalize(candidate, source_name)


def _mark_invalid(
    session: Session, record: DiscoveredRecord, reason: str, *, now: datetime
) -> RecordOutcome:
    record.resolution_status = ResolutionStatus.invalid
    record.resolution_error = reason
    record.resolved_at = now
    session.flush()
    logger.warning(
        "record could not be normalized",
        extra={"discovered_record_id": str(record.id), "reason": reason},
    )
    return RecordOutcome(kind=None, invalid_reason=reason)


def _lock(session: Session, business: Business) -> Business:
    """Re-read the business `FOR UPDATE` so two runs cannot write it at the same time."""
    locked = session.scalars(
        select(Business).where(Business.id == business.id).with_for_update()
    ).first()
    return locked or business


def _create_business(session: Session, normalized: NormalizedBusiness) -> Business:
    """A new business starts empty: survivorship fills it once the values are written."""
    business = Business(display_name=normalized.display_name)
    session.add(business)
    session.flush()
    return business


def _attach(
    session: Session,
    *,
    record: DiscoveredRecord,
    business: Business,
    normalized: NormalizedBusiness,
    now: datetime,
) -> None:
    """Link the record, write what it said, and recompute what the business shows."""
    record.business_id = business.id
    record.resolution_status = ResolutionStatus.linked
    record.resolution_error = None
    record.resolved_at = now
    survivorship.write_field_values(
        session,
        business=business,
        record=record,
        normalized=normalized,
        observed_at=record.last_discovered_at,
    )
    survivorship.recompute(session, business, now=now)
    session.flush()


def _queue_for_review(
    session: Session, record: DiscoveredRecord, decision: Decision, *, now: datetime
) -> None:
    """Write the top candidates and stop. No business is created for a pending pair."""
    for candidate in decision.candidates:
        _upsert_candidate(session, record, candidate)
    record.resolution_status = ResolutionStatus.needs_review
    record.resolution_error = None
    record.resolved_at = now
    session.flush()
    logger.info(
        "record sent to review",
        extra={
            "discovered_record_id": str(record.id),
            "candidates": len(decision.candidates),
            "capped_by_hard_rule": decision.capped_by_hard_rule,
        },
    )


def _upsert_candidate(
    session: Session, record: DiscoveredRecord, candidate: ScoredCandidate
) -> MatchCandidate:
    """One row per (record, business). A pair a human already decided is never reopened."""
    existing = session.scalars(
        select(MatchCandidate).where(
            MatchCandidate.discovered_record_id == record.id,
            MatchCandidate.business_id == candidate.business.id,
        )
    ).first()
    if existing is not None:
        if existing.status is MatchCandidateStatus.pending:
            existing.score = Decimal(str(round(candidate.match.score, 3)))
            existing.signals = candidate.match.signals.as_dict()
        return existing

    row = MatchCandidate(
        discovered_record_id=record.id,
        business_id=candidate.business.id,
        score=Decimal(str(round(candidate.match.score, 3))),
        signals=candidate.match.signals.as_dict(),
        status=MatchCandidateStatus.pending,
    )
    session.add(row)
    session.flush()
    return row


def _resolve_siblings(
    session: Session,
    record: DiscoveredRecord,
    *,
    keep: uuid.UUID | None,
    now: datetime,
    actor_id: uuid.UUID | None = None,
) -> list[MatchCandidate]:
    """Close every other pending candidate for this record once one has been chosen."""
    siblings = list(
        session.scalars(
            select(MatchCandidate).where(
                MatchCandidate.discovered_record_id == record.id,
                MatchCandidate.status == MatchCandidateStatus.pending,
            )
        )
    )
    closed: list[MatchCandidate] = []
    for sibling in siblings:
        if sibling.business_id == keep:
            continue
        sibling.status = MatchCandidateStatus.kept_apart
        sibling.decided_by = actor_id
        sibling.decided_at = now
        closed.append(sibling)
    session.flush()
    return closed


# --- one run ------------------------------------------------------------------------


def records_to_resolve(session: Session, job_run_id: uuid.UUID) -> list[DiscoveredRecord]:
    """The records a resolution run should look at, oldest sighting first.

    A record awaiting a human decision is left alone: re-running resolution must never
    overwrite a queue a reviewer is working through.
    """
    rows = list(
        session.scalars(
            select(DiscoveredRecord)
            .join(RecordSighting, RecordSighting.discovered_record_id == DiscoveredRecord.id)
            .where(RecordSighting.job_run_id == job_run_id)
            .order_by(RecordSighting.rank.asc(), DiscoveredRecord.id.asc())
        )
    )
    return [row for row in rows if _needs_resolution(row)]


def _needs_resolution(record: DiscoveredRecord) -> bool:
    if record.resolution_status is ResolutionStatus.needs_review:
        return False
    if record.resolution_status is ResolutionStatus.pending or record.resolved_at is None:
        return True
    # The payload moved on since we last looked at it.
    return record.updated_at > record.resolved_at


def summarize(outcomes: list[RecordOutcome]) -> ResolutionResultSummary:
    summary = ResolutionResultSummary(processed=len(outcomes))
    for outcome in outcomes:
        if outcome.invalid_reason is not None:
            summary.invalid += 1
        elif outcome.kind is DecisionKind.review:
            summary.needs_review += 1
        elif outcome.created:
            summary.created += 1
        else:
            summary.linked_existing += 1
    return summary


# --- reviewer decisions --------------------------------------------------------------


def get_candidate(session: Session, candidate_id: uuid.UUID) -> MatchCandidate:
    candidate = session.get(MatchCandidate, candidate_id)
    if candidate is None:
        raise NotFoundError(
            "Match candidate not found", details={"match_candidate_id": str(candidate_id)}
        )
    return candidate


def decide_candidate(
    session: Session,
    candidate_id: uuid.UUID,
    *,
    decision: str,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> MatchCandidate:
    """Apply a reviewer's `merge` or `keep_apart`. Both outcomes are audited."""
    moment = now or datetime.now(UTC)
    candidate = get_candidate(session, candidate_id)
    if candidate.status is not MatchCandidateStatus.pending:
        raise ConflictError(
            "This match candidate has already been decided",
            details={"status": candidate.status.value},
        )
    if decision not in {"merge", "keep_apart"}:
        raise ValidationFailedError(
            "A decision is either 'merge' or 'keep_apart'", details={"decision": decision}
        )

    record = session.get(DiscoveredRecord, candidate.discovered_record_id)
    if record is None:  # pragma: no cover - the FK cascade makes this unreachable
        raise NotFoundError("The record behind this candidate no longer exists")
    normalized = _normalize_record(session, record)

    candidate.decided_by = actor_id
    candidate.decided_at = moment
    linked: Business | None = None
    created = False

    if decision == "merge":
        candidate.status = MatchCandidateStatus.merged
        existing = session.get(Business, candidate.business_id)
        if existing is None:  # pragma: no cover - the FK cascade makes this unreachable
            raise NotFoundError("The business behind this candidate no longer exists")
        linked = _lock(session, existing)
        _attach(session, record=record, business=linked, normalized=normalized, now=moment)
        _resolve_siblings(session, record, keep=linked.id, now=moment, actor_id=actor_id)
    else:
        candidate.status = MatchCandidateStatus.kept_apart
        session.flush()
        # The last `keep_apart` is what says "this really is a business of its own".
        if _pending_count(session, record.id) == 0:
            linked = _create_business(session, normalized)
            created = True
            _attach(session, record=record, business=linked, normalized=normalized, now=moment)

    audit.record(
        session,
        action="match_candidate.decided",
        entity_type="match_candidate",
        entity_id=candidate.id,
        actor_id=actor_id,
        before={"status": MatchCandidateStatus.pending.value, "score": str(candidate.score)},
        after={
            "decision": decision,
            "business_id": str(linked.id) if linked is not None else None,
            "created_business": created,
            "discovered_record_id": str(record.id),
        },
    )
    session.flush()
    logger.info(
        "match candidate decided",
        extra={"match_candidate_id": str(candidate.id), "decision": decision},
    )
    return candidate


def _pending_count(session: Session, record_id: uuid.UUID) -> int:
    return len(
        list(
            session.scalars(
                select(MatchCandidate.id).where(
                    MatchCandidate.discovered_record_id == record_id,
                    MatchCandidate.status == MatchCandidateStatus.pending,
                )
            )
        )
    )


# --- reads ---------------------------------------------------------------------------


def list_candidates(
    session: Session,
    *,
    status: MatchCandidateStatus | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[MatchCandidateDetail]:
    """Pending pairs, newest first, with both sides shown so a reviewer can compare them."""
    stmt = (
        select(MatchCandidate)
        .order_by(MatchCandidate.created_at.desc(), MatchCandidate.id.desc())
        .limit(limit + 1)
    )
    if status is not None:
        stmt = stmt.where(MatchCandidate.status == status)
    stmt = apply_cursor(stmt, MatchCandidate.created_at, MatchCandidate.id, cursor)
    rows = list(session.scalars(stmt))

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[MatchCandidateDetail](
        items=[detail(session, row) for row in rows], next_cursor=next_cursor
    )


def detail(session: Session, candidate: MatchCandidate) -> MatchCandidateDetail:
    from app.modules.businesses import service as businesses_service
    from app.modules.discovery import service as discovery_service

    business = session.get(Business, candidate.business_id)
    record = session.get(DiscoveredRecord, candidate.discovered_record_id)
    source_name = ""
    if record is not None:
        source_name = session.scalar(select(Source.name).where(Source.id == record.source_id)) or ""

    return MatchCandidateDetail(
        id=candidate.id,
        status=candidate.status,
        score=candidate.score,
        signals=candidate.signals,
        decided_by=candidate.decided_by,
        decided_at=candidate.decided_at,
        created_at=candidate.created_at,
        discovered_record_id=candidate.discovered_record_id,
        business_id=candidate.business_id,
        record=(discovery_service.summarize(record, source_name) if record is not None else None),
        business=(businesses_service.summarize(business) if business is not None else None),
    )
