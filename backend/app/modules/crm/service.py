"""The rules of the CRM export. Everything a destination must not be trusted with lives here.

- **Human gate (non-negotiable):** a business is sent only if it has at least one
  `approved` opportunity and no active suppression — checked when the sync is scheduled
  and again immediately before every call to an adapter. Nothing else ever reaches one.
- **Delay:** a fresh approval waits `CRM_SYNC_DELAY_MINUTES` (the review undo window), so an
  approval that is undone in time never leaves. Each approval waits its own window: a sync
  sends the ones that are ready and comes back for the rest.
- **One record per business**, whatever the number of approved services; later approvals
  update the same record and never touch the CRM-owned fields.
- **Dedupe** by the stored id, then by the CRM's own search (business id, domain, phone).
- **Unchanged payload** → no call. **Transient failure** → backoff, three attempts, then
  `held`. Auth, config and rejected → `held` at once. Every attempt is a row and an audit
  entry.
- **Suppression** after a sync flags the record as Do not contact; lifting it clears the
  flag. A record is never deleted.
"""

import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger, scrub
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.modules.audit import service as audit_log
from app.modules.auth.models import User
from app.modules.businesses.models import Business
from app.modules.compliance import service as compliance
from app.modules.crm import adapter as adapters
from app.modules.crm.adapter import CrmAdapter, CrmError, CrmRecord, CrmResult
from app.modules.crm.csv_adapter import EXPORTED_STATUSES, export_filename, render_csv
from app.modules.crm.fields import LABEL_BY_KEY, STATUS_NEW, STATUS_WITHDRAWN
from app.modules.crm.models import (
    CrmLead,
    CrmLeadOpportunity,
    CrmLeadStatus,
    CrmSyncAction,
    CrmSyncAttempt,
    CrmSyncStatus,
)
from app.modules.crm.payload import (
    approved_opportunities,
    build_record,
    format_phone,
    payload_hash,
    service_label,
)
from app.modules.crm.schemas import (
    CrmCheckRead,
    CrmHealthRead,
    CrmLeadRead,
    CrmLeadStatusRead,
    CrmStatusRead,
    CrmSyncAttemptRead,
    ExportScope,
    SyncAllResult,
)
from app.modules.opportunities.models import Opportunity, ReviewStatus

logger = get_logger("app.crm")

OPEN_STATUSES = (CrmLeadStatus.scheduled, CrmLeadStatus.syncing, CrmLeadStatus.held)
MAX_ERROR_CHARS = 1000
DNC_NOTE = "Marked do not contact in Lead Discovery Radar"


# --- eligibility (the gate) ------------------------------------------------------------------


@dataclass(frozen=True)
class Eligibility:
    """What the gate saw for one business at one instant."""

    approved: list[Opportunity]
    ready: list[Opportunity]
    suppressed: bool
    next_ready_at: datetime | None

    @property
    def eligible(self) -> bool:
        return bool(self.approved) and not self.suppressed

    @property
    def sendable(self) -> bool:
        return bool(self.ready) and not self.suppressed


def eligibility(
    session: Session,
    business: Business,
    *,
    now: datetime,
    settings: Settings,
    ignore_delay: bool = False,
    carried: set[uuid.UUID] | None = None,
) -> Eligibility:
    """`carried` are the approvals the CRM record already holds (sent early with Send now):
    they stay in step with the database whatever the delay says."""
    approved = approved_opportunities(session, business.id)
    if ignore_delay:
        ready, waiting = list(approved), []
    else:
        cutoff = now - timedelta(minutes=settings.resolved_crm_sync_delay_minutes)
        already = carried or set()
        ready = [
            o
            for o in approved
            if o.id in already or (o.decided_at is not None and o.decided_at <= cutoff)
        ]
        waiting = [o for o in approved if o not in ready]
    next_ready = min((o.decided_at for o in waiting if o.decided_at is not None), default=None)
    return Eligibility(
        approved=approved,
        ready=ready,
        suppressed=compliance.is_suppressed(session, business),
        next_ready_at=(
            next_ready + timedelta(minutes=settings.resolved_crm_sync_delay_minutes)
            if next_ready is not None
            else None
        ),
    )


# --- scheduling ------------------------------------------------------------------------------


def get_lead(session: Session, business_id: uuid.UUID, destination: str) -> CrmLead | None:
    return session.scalars(
        select(CrmLead).where(
            CrmLead.business_id == business_id, CrmLead.destination == destination
        )
    ).first()


def on_business_changed(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
    approval: bool = False,
    settings: Settings | None = None,
) -> CrmLead | None:
    """Something that matters to the CRM happened to this business; decide what to schedule.

    Called after an approval (`approval=True`), an undo, a do-not-contact, a suppression
    added or lifted. A business that was never sent and is not eligible has nothing to
    sync; one already in the CRM gets every change applied as soon as it is due.
    """
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    destination = config.crm_destination
    business = session.get(Business, business_id)
    if business is None:
        return None
    state = eligibility(session, business, now=moment, settings=config)
    lead = get_lead(session, business_id, destination)
    delay = timedelta(minutes=config.resolved_crm_sync_delay_minutes)

    if lead is None:
        if not state.eligible:
            return None
        lead = CrmLead(
            business_id=business.id,
            destination=destination,
            status=CrmLeadStatus.scheduled,
            due_at=_first_due(state, moment, delay),
        )
        session.add(lead)
        session.flush()
        _audit(session, lead, "crm_lead.scheduled", actor_id, {"reason": "approval"})
        return lead

    if lead.external_id is None:
        if not state.eligible:
            if lead.status is not CrmLeadStatus.cancelled:
                _set_status(lead, CrmLeadStatus.cancelled, due_at=None)
                _audit(
                    session,
                    lead,
                    "crm_lead.cancelled",
                    actor_id,
                    {"reason": "suppressed" if state.suppressed else "no approved opportunity"},
                )
            return lead
        due = _first_due(state, moment, delay)
        if lead.status is CrmLeadStatus.scheduled and lead.due_at is not None:
            due = min(lead.due_at, due)
        _reschedule(session, lead, due, actor_id, reason="approval" if approval else "change")
        return lead

    # Already in the CRM: an update, a withdrawal or a do-not-contact flag is applied as soon
    # as the worker gets to it; a new approval still waits for its own undo window.
    due = _first_due(state, moment, delay) if approval and not state.ready else moment
    if lead.status is CrmLeadStatus.scheduled and lead.due_at is not None:
        due = min(lead.due_at, due)
    _reschedule(session, lead, due, actor_id, reason="approval" if approval else "change")
    return lead


def _first_due(state: Eligibility, now: datetime, delay: timedelta) -> datetime:
    """When the first approval that is not ready yet will be; now if one already is."""
    if state.ready:
        return now
    earliest = min((o.decided_at for o in state.approved if o.decided_at is not None), default=now)
    return max(earliest + delay, now)


def _reschedule(
    session: Session, lead: CrmLead, due: datetime, actor_id: uuid.UUID | None, *, reason: str
) -> None:
    before = lead.status.value
    _set_status(lead, CrmLeadStatus.scheduled, due_at=due)
    lead.attempts = 0
    lead.last_error = None
    session.flush()
    _audit(
        session,
        lead,
        "crm_lead.scheduled",
        actor_id,
        {"reason": reason, "from": before, "due_at": due.isoformat()},
    )


def _set_status(lead: CrmLead, status: CrmLeadStatus, *, due_at: datetime | None) -> None:
    lead.status = status
    lead.due_at = due_at


# --- syncing ---------------------------------------------------------------------------------


def sync_lead(
    session: Session,
    lead: CrmLead,
    *,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
    adapter: CrmAdapter | None = None,
    ignore_delay: bool = False,
) -> CrmLead:
    """Bring one lead's CRM record in line with the database, with the gate re-checked first.

    `ignore_delay` is Send now: it skips the undo-window wait but never the gate.
    """
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    business = session.get(Business, lead.business_id)
    if business is None:
        _set_status(lead, CrmLeadStatus.cancelled, due_at=None)
        session.flush()
        return lead
    state = eligibility(
        session,
        business,
        now=moment,
        settings=config,
        ignore_delay=ignore_delay,
        carried=_carried(session, lead),
    )
    crm = (
        adapter
        if adapter is not None
        else adapters.build(session, lead.destination, settings=config)
    )

    _set_status(lead, CrmLeadStatus.syncing, due_at=None)
    session.flush()

    if state.suppressed:
        if lead.external_id is None:
            return _cancel(session, lead, actor_id, reason="suppressed")
        if lead.do_not_contact_sent:
            return _finish(session, lead, moment, actor_id, CrmSyncAction.unchanged, None, 0)
        return _attempt(
            session,
            lead,
            CrmSyncAction.mark_dnc,
            lambda: crm.mark_do_not_contact(lead.external_id or "", DNC_NOTE, flag=True),
            actor_id=actor_id,
            now=moment,
            settings=config,
            after=lambda result: _mark_dnc_done(lead, True),
        )

    if not state.approved:
        if lead.external_id is None:
            return _cancel(session, lead, actor_id, reason="no approved opportunity")
        if lead.status is CrmLeadStatus.withdrawn or (
            lead.payload_hash is None and not lead.do_not_contact_sent
        ):
            return _finish(session, lead, moment, actor_id, CrmSyncAction.unchanged, None, 0)
        return _attempt(
            session,
            lead,
            CrmSyncAction.withdraw,
            lambda: _withdraw(crm, lead),
            actor_id=actor_id,
            now=moment,
            settings=config,
            after=lambda result: _withdrawn(session, lead),
            final_status=CrmLeadStatus.withdrawn,
        )

    if not state.ready:
        # Every approval is still inside its undo window: come back when the first is out.
        _set_status(lead, CrmLeadStatus.scheduled, due_at=state.next_ready_at or moment)
        session.flush()
        return lead

    record = build_record(session, business, state.ready, settings=config, do_not_contact=False)
    digest = payload_hash(record)

    if lead.external_id is None:
        found = _link_existing(
            session, lead, crm, business, actor_id=actor_id, now=moment, settings=config
        )
        if found is False:
            # The lookup failed; the attempt row says why, and the lead is scheduled or held.
            return lead

    if (
        lead.external_id is not None
        and lead.payload_hash == digest
        and not lead.do_not_contact_sent
        and lead.status is not CrmLeadStatus.withdrawn
    ):
        _carry(session, lead, state.ready)
        return _finish(
            session, lead, moment, actor_id, CrmSyncAction.unchanged, None, 0, next_ready=state
        )

    action = CrmSyncAction.update if lead.external_id is not None else CrmSyncAction.create

    def apply(result: CrmResult) -> None:
        lead.external_id = result.external_id
        lead.external_url = result.external_url
        lead.payload_hash = digest
        lead.do_not_contact_sent = False
        lead.export_batch_id = None
        _carry(session, lead, state.ready)

    return _attempt(
        session,
        lead,
        action,
        lambda: crm.upsert(record, lead.external_id),
        actor_id=actor_id,
        now=moment,
        settings=config,
        after=apply,
        next_ready=state,
    )


def _link_existing(
    session: Session,
    lead: CrmLead,
    crm: CrmAdapter,
    business: Business,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
    settings: Settings,
) -> bool:
    """Ask the CRM whether it already has this business. True/None = go on; False = failed."""
    started = time.perf_counter()
    try:
        found = crm.find_by_keys(
            str(business.id), business.domain, format_phone(business.phone_e164)
        )
    except Exception as exc:  # every failure is data on the attempt row
        _fail(
            session,
            lead,
            CrmSyncAction.link,
            exc,
            started,
            actor_id=actor_id,
            now=now,
            settings=settings,
        )
        return False
    if found is None:
        return True
    lead.external_id = found
    lead.payload_hash = None  # whatever is there was not written by this build; update it
    _record_attempt(
        session,
        lead,
        CrmSyncAction.link,
        CrmSyncStatus.ok,
        None,
        None,
        _elapsed(started),
        actor_id=actor_id,
        extra={"external_id": found},
    )
    logger.info(
        "linked an existing CRM record instead of creating one",
        extra={"crm_lead_id": str(lead.id), "external_id": found},
    )
    return True


def _withdraw(crm: CrmAdapter, lead: CrmLead) -> CrmResult:
    result = crm.withdraw(lead.external_id or "")
    if lead.do_not_contact_sent:
        # The suppression that flagged it was lifted too; the flag goes with it.
        crm.mark_do_not_contact(lead.external_id or "", DNC_NOTE, flag=False)
    return result


def _withdrawn(session: Session, lead: CrmLead) -> None:
    lead.do_not_contact_sent = False
    lead.export_batch_id = None
    session.execute(delete(CrmLeadOpportunity).where(CrmLeadOpportunity.crm_lead_id == lead.id))


def _mark_dnc_done(lead: CrmLead, flag: bool) -> None:
    lead.do_not_contact_sent = flag
    lead.export_batch_id = None


def _carried(session: Session, lead: CrmLead) -> set[uuid.UUID]:
    return set(
        session.scalars(
            select(CrmLeadOpportunity.opportunity_id).where(
                CrmLeadOpportunity.crm_lead_id == lead.id
            )
        )
    )


def _carry(session: Session, lead: CrmLead, rows: Sequence[Opportunity]) -> None:
    """Record which approved opportunities the CRM record now carries."""
    wanted = {row.id for row in rows}
    current = set(
        session.scalars(
            select(CrmLeadOpportunity.opportunity_id).where(
                CrmLeadOpportunity.crm_lead_id == lead.id
            )
        )
    )
    for opportunity_id in current - wanted:
        session.execute(
            delete(CrmLeadOpportunity).where(
                CrmLeadOpportunity.crm_lead_id == lead.id,
                CrmLeadOpportunity.opportunity_id == opportunity_id,
            )
        )
    for opportunity_id in wanted - current:
        session.add(CrmLeadOpportunity(crm_lead_id=lead.id, opportunity_id=opportunity_id))
    session.flush()


def _attempt(
    session: Session,
    lead: CrmLead,
    action: CrmSyncAction,
    call: Any,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
    settings: Settings,
    after: Any,
    final_status: CrmLeadStatus = CrmLeadStatus.synced,
    next_ready: Eligibility | None = None,
) -> CrmLead:
    started = time.perf_counter()
    try:
        result: CrmResult = call()
    except Exception as exc:  # every failure is data on the attempt row
        return _fail(
            session, lead, action, exc, started, actor_id=actor_id, now=now, settings=settings
        )
    after(result)
    recorded = action
    if result.action == "unchanged" and action is CrmSyncAction.withdraw:
        recorded = CrmSyncAction.withdraw
    return _finish(
        session,
        lead,
        now,
        actor_id,
        recorded,
        result,
        _elapsed(started),
        final_status=final_status,
        next_ready=next_ready,
    )


def _finish(
    session: Session,
    lead: CrmLead,
    now: datetime,
    actor_id: uuid.UUID | None,
    action: CrmSyncAction,
    result: CrmResult | None,
    duration_ms: int,
    *,
    final_status: CrmLeadStatus = CrmLeadStatus.synced,
    next_ready: Eligibility | None = None,
) -> CrmLead:
    lead.attempts = 0
    lead.last_error = None
    lead.last_synced_at = now
    if next_ready is not None and next_ready.next_ready_at is not None:
        # More approvals are waiting for their undo window; come back for them.
        _set_status(lead, CrmLeadStatus.scheduled, due_at=next_ready.next_ready_at)
    else:
        _set_status(lead, final_status, due_at=None)
    _record_attempt(
        session,
        lead,
        action,
        CrmSyncStatus.ok,
        None,
        None,
        duration_ms,
        actor_id=actor_id,
        extra={
            "result": result.action if result is not None else "unchanged",
            "external_id": lead.external_id,
        },
    )
    logger.info(
        "crm lead synced",
        extra={
            "crm_lead_id": str(lead.id),
            "business_id": str(lead.business_id),
            "action": action.value,
            "status": lead.status.value,
        },
    )
    return lead


def _fail(
    session: Session,
    lead: CrmLead,
    action: CrmSyncAction,
    exc: BaseException,
    started: float,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
    settings: Settings,
) -> CrmLead:
    message = scrub(f"{type(exc).__name__}: {exc}")[:MAX_ERROR_CHARS]
    retryable = isinstance(exc, CrmError) and exc.retryable
    http_status = exc.http_status if isinstance(exc, CrmError) else None
    lead.attempts += 1
    lead.last_error = message
    if retryable and lead.attempts < settings.crm_sync_max_attempts:
        retry_after = exc.retry_after_seconds if isinstance(exc, CrmError) else None
        wait = retry_after if retry_after is not None else _backoff(lead.attempts, settings)
        _set_status(lead, CrmLeadStatus.scheduled, due_at=now + timedelta(seconds=wait))
    else:
        _set_status(lead, CrmLeadStatus.held, due_at=None)
    _record_attempt(
        session,
        lead,
        action,
        CrmSyncStatus.failed,
        http_status,
        message,
        _elapsed(started),
        actor_id=actor_id,
        extra={"attempt": lead.attempts, "retryable": retryable},
    )
    logger.warning(
        "crm sync attempt failed",
        extra={
            "crm_lead_id": str(lead.id),
            "action": action.value,
            "attempt": lead.attempts,
            "status": lead.status.value,
            "http_status": http_status,
        },
    )
    return lead


def _backoff(attempt: int, settings: Settings) -> float:
    return float(settings.crm_sync_backoff_seconds * (2 ** max(attempt - 1, 0)))


def _cancel(session: Session, lead: CrmLead, actor_id: uuid.UUID | None, *, reason: str) -> CrmLead:
    _set_status(lead, CrmLeadStatus.cancelled, due_at=None)
    session.flush()
    _audit(session, lead, "crm_lead.cancelled", actor_id, {"reason": reason})
    return lead


def _record_attempt(
    session: Session,
    lead: CrmLead,
    action: CrmSyncAction,
    status: CrmSyncStatus,
    http_status: int | None,
    error: str | None,
    duration_ms: int,
    *,
    actor_id: uuid.UUID | None,
    extra: dict[str, Any] | None = None,
) -> CrmSyncAttempt:
    row = CrmSyncAttempt(
        crm_lead_id=lead.id,
        action=action,
        status=status,
        http_status=http_status,
        error=error,
        duration_ms=duration_ms,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    _audit(
        session,
        lead,
        f"crm.{action.value}",
        actor_id,
        {
            "attempt_id": row.id,
            "status": status.value,
            "http_status": http_status,
            "error": error,
            "lead_status": lead.status.value,
            **(extra or {}),
        },
    )
    return row


def _audit(
    session: Session,
    lead: CrmLead,
    action: str,
    actor_id: uuid.UUID | None,
    after: dict[str, Any],
) -> None:
    audit_log.record(
        session,
        action=action,
        entity_type="crm_lead",
        entity_id=lead.id,
        actor_id=actor_id,
        after={
            "business_id": str(lead.business_id),
            "destination": lead.destination,
            **{k: v for k, v in after.items() if v is not None},
        },
    )


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


# --- what the worker and the buttons call ------------------------------------------------------


def due_leads(
    session: Session, *, now: datetime, destination: str, limit: int = 100
) -> list[CrmLead]:
    return list(
        session.scalars(
            select(CrmLead)
            .where(
                CrmLead.destination == destination,
                CrmLead.status == CrmLeadStatus.scheduled,
                CrmLead.due_at.is_not(None),
                CrmLead.due_at <= now,
            )
            .order_by(CrmLead.due_at.asc(), CrmLead.id.asc())
            .limit(limit)
        )
    )


def sync_due(
    session: Session,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    limit: int = 100,
    adapter: CrmAdapter | None = None,
) -> int:
    """Send every scheduled lead whose time has come. Returns how many were processed."""
    config = settings or get_settings()
    if not config.crm_auto_sync:
        return 0
    moment = now or datetime.now(UTC)
    rows = due_leads(session, now=moment, destination=config.crm_destination, limit=limit)
    for lead in rows:
        sync_lead(session, lead, now=moment, settings=config, adapter=adapter)
        session.commit()
    return len(rows)


def sync_now(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> CrmLeadRead:
    """Send now: skips the undo-window wait, never the gate."""
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    business = session.get(Business, business_id)
    if business is None:
        raise NotFoundError("Business not found", details={"business_id": str(business_id)})
    state = eligibility(session, business, now=moment, settings=config, ignore_delay=True)
    lead = get_lead(session, business_id, config.crm_destination)
    if not state.eligible and (lead is None or lead.external_id is None):
        raise ConflictError(
            "This business has no approved opportunity or is suppressed; it cannot be sent",
            code="not_eligible",
            details={
                "business_id": str(business_id),
                "approved": len(state.approved),
                "suppressed": state.suppressed,
            },
        )
    if lead is None:
        lead = CrmLead(
            business_id=business.id,
            destination=config.crm_destination,
            status=CrmLeadStatus.scheduled,
            due_at=moment,
        )
        session.add(lead)
        session.flush()
        _audit(session, lead, "crm_lead.scheduled", actor.id, {"reason": "send now"})
    sync_lead(session, lead, actor_id=actor.id, now=moment, settings=config, ignore_delay=True)
    session.flush()
    return read_lead(session, lead)


def retry(
    session: Session,
    crm_lead_id: uuid.UUID,
    *,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> CrmLeadRead:
    """Try a held lead again, right now. Only a held lead can be retried."""
    config = settings or get_settings()
    lead = get_crm_lead(session, crm_lead_id)
    if lead.status is not CrmLeadStatus.held:
        raise ConflictError(
            f"Only a held lead can be retried; this one is '{lead.status.value}'",
            code="not_held",
            details={"crm_lead_id": str(lead.id), "status": lead.status.value},
        )
    lead.attempts = 0
    sync_lead(session, lead, actor_id=actor.id, now=now, settings=config)
    session.flush()
    return read_lead(session, lead)


def sync_all(
    session: Session,
    *,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> SyncAllResult:
    """Every due or held lead, plus eligible businesses nobody scheduled yet (pre-v0.7.0
    approvals). Approvals still inside their undo window are left alone."""
    config = settings or get_settings()
    moment = now or datetime.now(UTC)
    destination = config.crm_destination
    rows = list(
        session.scalars(
            select(CrmLead)
            .where(
                CrmLead.destination == destination,
                (
                    (CrmLead.status == CrmLeadStatus.scheduled)
                    & CrmLead.due_at.is_not(None)
                    & (CrmLead.due_at <= moment)
                )
                | (CrmLead.status == CrmLeadStatus.held),
            )
            .order_by(CrmLead.due_at.asc().nulls_last(), CrmLead.id.asc())
        )
    )
    rows.extend(_unscheduled_eligible(session, destination=destination))
    result = SyncAllResult(considered=len(rows), synced=0, held=0, scheduled=0, cancelled=0)
    for lead in rows:
        lead.attempts = 0
        sync_lead(session, lead, actor_id=actor.id, now=moment, settings=config)
        if lead.status is CrmLeadStatus.synced or lead.status is CrmLeadStatus.withdrawn:
            result.synced += 1
        elif lead.status is CrmLeadStatus.held:
            result.held += 1
        elif lead.status is CrmLeadStatus.scheduled:
            result.scheduled += 1
        else:
            result.cancelled += 1
    session.flush()
    return result


def _unscheduled_eligible(session: Session, *, destination: str) -> list[CrmLead]:
    """Businesses with an approved opportunity and no lead row at this destination."""
    from app.modules.opportunities.models import ReviewStatus

    scheduled = select(CrmLead.business_id).where(CrmLead.destination == destination)
    stmt: Select[Any] = (
        select(Business)
        .where(
            Business.id.in_(
                select(Opportunity.business_id).where(
                    Opportunity.review_status == ReviewStatus.approved
                )
            ),
            Business.id.not_in(scheduled),
            compliance.not_suppressed(),
        )
        .order_by(Business.created_at.asc(), Business.id.asc())
    )
    created: list[CrmLead] = []
    for business in session.scalars(stmt):
        lead = CrmLead(
            business_id=business.id,
            destination=destination,
            status=CrmLeadStatus.scheduled,
            due_at=datetime.now(UTC),
        )
        session.add(lead)
        created.append(lead)
    session.flush()
    return created


# --- suppression propagation (called by compliance) ------------------------------------------


def on_suppression_changed(
    session: Session,
    business_id: uuid.UUID | None,
    *,
    actor_id: uuid.UUID | None,
    now: datetime | None = None,
) -> None:
    """A suppression was added or lifted: schedule the flag (or its removal) for the record."""
    if business_id is None:
        return
    on_business_changed(session, business_id, actor_id=actor_id, now=now)


# --- reads --------------------------------------------------------------------------------------


def get_crm_lead(session: Session, crm_lead_id: uuid.UUID) -> CrmLead:
    lead = session.get(CrmLead, crm_lead_id)
    if lead is None:
        raise NotFoundError("CRM lead not found", details={"crm_lead_id": str(crm_lead_id)})
    return lead


def read_lead(session: Session, lead: CrmLead, business: Business | None = None) -> CrmLeadRead:
    from app.modules.discovery import providers

    row = business or session.get(Business, lead.business_id)
    assert row is not None  # the FK cascades
    services = [
        service_label(service)
        for service in session.scalars(
            select(Opportunity.service)
            .join(CrmLeadOpportunity, CrmLeadOpportunity.opportunity_id == Opportunity.id)
            .where(CrmLeadOpportunity.crm_lead_id == lead.id)
            .order_by(Opportunity.score.desc())
        )
    ]
    if not services and lead.status in {CrmLeadStatus.scheduled, CrmLeadStatus.held}:
        # Nothing has been carried to the CRM yet: show what the approvals say will go.
        services = [
            service_label(service)
            for service in session.scalars(
                select(Opportunity.service)
                .where(
                    Opportunity.business_id == lead.business_id,
                    Opportunity.review_status == ReviewStatus.approved,
                )
                .order_by(Opportunity.score.desc())
            )
        ]
    return CrmLeadRead(
        id=lead.id,
        business_id=lead.business_id,
        business_name=row.display_name,
        city=row.city,
        state=row.state,
        destination=lead.destination,
        status=lead.status,
        external_id=lead.external_id,
        external_url=lead.external_url,
        due_at=lead.due_at,
        attempts=lead.attempts,
        last_error=lead.last_error,
        last_synced_at=lead.last_synced_at,
        export_batch_id=lead.export_batch_id,
        services=services,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        data_providers=providers.data_providers(session, [lead.business_id]).get(
            lead.business_id, []
        ),
    )


def list_leads(
    session: Session,
    *,
    status: CrmLeadStatus | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    settings: Settings | None = None,
) -> Page[CrmLeadRead]:
    config = settings or get_settings()
    stmt: Select[Any] = (
        select(CrmLead, Business)
        .join(Business, Business.id == CrmLead.business_id)
        .where(CrmLead.destination == config.crm_destination)
        .order_by(CrmLead.created_at.desc(), CrmLead.id.desc())
        .limit(limit + 1)
    )
    if status is not None:
        stmt = stmt.where(CrmLead.status == status)
    stmt = apply_cursor(stmt, CrmLead.created_at, CrmLead.id, cursor)
    rows = list(session.execute(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1][0]
        next_cursor = encode_cursor(last.created_at, last.id)
    return Page[CrmLeadRead](
        items=[read_lead(session, lead, business) for lead, business in rows],
        next_cursor=next_cursor,
    )


def attempts(session: Session, crm_lead_id: uuid.UUID) -> list[CrmSyncAttemptRead]:
    lead = get_crm_lead(session, crm_lead_id)
    return attempts_for(session, lead.id)


def attempts_for(session: Session, crm_lead_id: uuid.UUID) -> list[CrmSyncAttemptRead]:
    rows = session.scalars(
        select(CrmSyncAttempt)
        .where(CrmSyncAttempt.crm_lead_id == crm_lead_id)
        .order_by(CrmSyncAttempt.id.desc())
    )
    return [
        CrmSyncAttemptRead(
            id=row.id,
            crm_lead_id=row.crm_lead_id,
            action=row.action,
            status=row.status,
            http_status=row.http_status,
            error=row.error,
            duration_ms=row.duration_ms,
            created_at=row.created_at,
        )
        for row in rows
    ]


def status(session: Session, *, settings: Settings | None = None) -> CrmStatusRead:
    config = settings or get_settings()
    destination = config.crm_destination
    try:
        health = adapters.build(session, destination, settings=config).check()
        health_read = CrmHealthRead(
            destination=health.destination,
            ok=health.ok,
            checks=[CrmCheckRead(name=c.name, ok=c.ok, detail=c.detail) for c in health.checks],
            message=health.message,
        )
    except CrmError as exc:
        health_read = CrmHealthRead(
            destination=destination,
            ok=False,
            checks=[CrmCheckRead(name="config", ok=False, detail=exc.message)],
            message=exc.message,
        )
    counts = {item.value: 0 for item in CrmLeadStatus}
    for value, count in session.execute(
        select(CrmLead.status, func.count())
        .where(CrmLead.destination == destination)
        .group_by(CrmLead.status)
    ).all():
        counts[value.value] = int(count)
    return CrmStatusRead(
        destination=destination,
        demo=destination == adapters.Destination.fake.value,
        auto_sync=config.crm_auto_sync,
        sync_delay_minutes=config.resolved_crm_sync_delay_minutes,
        health=health_read,
        counts=counts,
    )


def status_blocks(
    session: Session, business_ids: Sequence[uuid.UUID], *, settings: Settings | None = None
) -> dict[uuid.UUID, CrmLeadStatusRead]:
    """The `crm` block for the leads endpoints, by business."""
    config = settings or get_settings()
    wanted = set(business_ids)
    if not wanted:
        return {}
    rows = session.scalars(
        select(CrmLead).where(
            CrmLead.destination == config.crm_destination, CrmLead.business_id.in_(wanted)
        )
    )
    return {
        row.business_id: CrmLeadStatusRead(
            id=row.id,
            status=row.status,
            external_url=row.external_url,
            last_synced_at=row.last_synced_at,
            due_at=row.due_at,
            last_error=row.last_error,
        )
        for row in rows
    }


# --- CSV export -----------------------------------------------------------------------------------


def export_csv(
    session: Session,
    *,
    scope: ExportScope,
    actor: User,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> tuple[str, Iterator[bytes]]:
    """Render the CSV destination's records and mark them exported.

    `new` = synced (or withdrawn) rows never put in a file, or changed since the last one;
    `all` = every row that has reached the destination. Values are built fresh, so a
    business suppressed after its sync reads Do not contact = true, and a withdrawn one has
    no services and Status Withdrawn. Nothing that never passed the gate is here.
    """
    config = settings or get_settings()
    if config.crm_destination != adapters.Destination.csv.value:
        raise ValidationFailedError(
            "CSV export is available only when CRM_DESTINATION=csv",
            details={"destination": config.crm_destination},
        )
    moment = now or datetime.now(UTC)
    stmt = (
        select(CrmLead, Business)
        .join(Business, Business.id == CrmLead.business_id)
        .where(
            CrmLead.destination == adapters.Destination.csv.value,
            CrmLead.status.in_(EXPORTED_STATUSES),
        )
        .order_by(CrmLead.last_synced_at.asc().nulls_last(), CrmLead.id.asc())
    )
    if scope == "new":
        stmt = stmt.where(CrmLead.export_batch_id.is_(None))
    rows = list(session.execute(stmt))
    batch_id = uuid.uuid4().hex
    rendered: list[dict[str, Any]] = []
    for lead, business in rows:
        carried = list(
            session.scalars(
                select(Opportunity)
                .join(CrmLeadOpportunity, CrmLeadOpportunity.opportunity_id == Opportunity.id)
                .where(CrmLeadOpportunity.crm_lead_id == lead.id)
                .order_by(Opportunity.score.desc(), Opportunity.id.asc())
            )
        )
        record = build_record(
            session,
            business,
            carried,
            settings=config,
            status=STATUS_WITHDRAWN if lead.status is CrmLeadStatus.withdrawn else STATUS_NEW,
        )
        rendered.append(_labeled_row(record))
        lead.export_batch_id = batch_id
        _record_attempt(
            session,
            lead,
            CrmSyncAction.export,
            CrmSyncStatus.ok,
            None,
            None,
            0,
            actor_id=actor.id,
            extra={"batch_id": batch_id, "scope": scope},
        )
    session.flush()
    logger.info(
        "csv export rendered",
        extra={"batch_id": batch_id, "scope": scope, "rows": len(rendered)},
    )
    return export_filename(moment), render_csv(rendered)


def _labeled_row(record: CrmRecord) -> dict[str, Any]:
    return {LABEL_BY_KEY[key]: value for key, value in record.fields.items() if key in LABEL_BY_KEY}


__all__ = [
    "attempts",
    "eligibility",
    "export_csv",
    "list_leads",
    "on_business_changed",
    "on_suppression_changed",
    "retry",
    "status",
    "status_blocks",
    "sync_all",
    "sync_due",
    "sync_lead",
    "sync_now",
]
