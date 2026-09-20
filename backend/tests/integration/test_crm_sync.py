"""The CRM sync rules against the fake destination: gate, delay, one record per business,
ownership, dedupe, unchanged, retry/hold, undo after sync, suppression propagation."""

import uuid
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.compliance import service as compliance
from app.modules.compliance.models import SuppressionSource
from app.modules.compliance.schemas import SuppressionCreate
from app.modules.crm import adapter as adapters
from app.modules.crm import service
from app.modules.crm.adapter import (
    CrmAdapter,
    CrmAuthError,
    CrmRecord,
    CrmResult,
    CrmTransientError,
)
from app.modules.crm.fields import LABEL_BY_KEY
from app.modules.crm.models import (
    CrmLead,
    CrmLeadOpportunity,
    CrmLeadStatus,
    CrmSyncAction,
    CrmSyncAttempt,
    CrmSyncStatus,
    FakeCrmRecord,
)
from app.modules.crm.worker import sync_crm_lead, sync_due
from app.modules.opportunities.models import Opportunity, ReviewStatus
from app.modules.review import service as review
from app.modules.review.models import Decision
from app.modules.review.schemas import ReviewRequest
from tests.conftest import make_user
from tests.integration.test_classification_run import make_audit, make_business
from tests.integration.test_review_api import make_opportunity

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
DELAY = timedelta(minutes=30)
LABEL = LABEL_BY_KEY


# --- a spy around any adapter ---------------------------------------------------------------------


class SpyAdapter:
    """Records every call and can be told to fail the next ones."""

    def __init__(
        self, inner: CrmAdapter, calls: list[tuple[str, Any]], failures: deque[Exception]
    ) -> None:
        self._inner = inner
        self.name = inner.name
        self._calls = calls
        self._failures = failures

    def _hit(self, method: str, *args: Any) -> None:
        self._calls.append((method, args))
        # Failures are injected on writes only, so a test sees the create/update path fail
        # rather than the dedupe lookup in front of it.
        if self._failures and method != "find_by_keys":
            raise self._failures.popleft()

    def check(self) -> Any:
        return self._inner.check()

    def upsert(self, record: CrmRecord, existing_external_id: str | None) -> CrmResult:
        self._hit("upsert", record, existing_external_id)
        return self._inner.upsert(record, existing_external_id)

    def find_by_keys(self, radar_business_id: str, domain: str | None, phone: str | None) -> Any:
        self._hit("find_by_keys", radar_business_id, domain, phone)
        return self._inner.find_by_keys(radar_business_id, domain, phone)

    def mark_do_not_contact(self, external_id: str, note: str, *, flag: bool = True) -> CrmResult:
        self._hit("mark_do_not_contact", external_id, flag)
        return self._inner.mark_do_not_contact(external_id, note, flag=flag)

    def withdraw(self, external_id: str) -> CrmResult:
        self._hit("withdraw", external_id)
        return self._inner.withdraw(external_id)


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.failures: deque[Exception] = deque()

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def install(self, destination: str) -> None:
        original = adapters.snapshot()[destination]
        adapters.register(
            destination,
            lambda session, settings: SpyAdapter(
                original(session, settings), self.calls, self.failures
            ),
            replace=True,
        )


@pytest.fixture(autouse=True)
def _fake_destination(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """These tests run against the fake destination; the rest of the suite keeps the default."""
    monkeypatch.setenv("CRM_DESTINATION", "fake")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def spy() -> Iterator[Spy]:
    saved = adapters.snapshot()
    watcher = Spy()
    watcher.install("fake")
    yield watcher
    adapters.restore(saved)


# --- helpers --------------------------------------------------------------------------------------


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer, "reviewer@example.com")


@pytest.fixture
def rep(db: Session) -> User:
    return make_user(db, Role.sales_rep, "rep1@example.com")


@pytest.fixture
def business(db: Session) -> Business:
    row = make_business(db, name="Barton Creek Plumbing")
    row.domain = "bartoncreekplumbing.invalid"
    row.phone_e164 = "+15125550102"
    make_audit(db, row)
    db.commit()
    return row


def approve(
    session: Session,
    reviewer: User,
    opportunity: Opportunity,
    *,
    now: datetime,
    rep: User | None = None,
    note: str | None = None,
) -> uuid.UUID:
    decided = review.decide(
        session,
        opportunity.id,
        ReviewRequest(
            decision=Decision.approve,
            lock_version=opportunity.lock_version,
            assigned_to=rep.id if rep else None,
            note=note,
        ),
        actor=reviewer,
        now=now,
    )
    session.commit()
    return decided.decision.id


def undo(session: Session, reviewer: User, decision_id: uuid.UUID, *, now: datetime) -> None:
    review.undo(session, decision_id, actor=reviewer, now=now)
    session.commit()


def lead_for(session: Session, business: Business) -> CrmLead:
    session.expire_all()
    row = service.get_lead(session, business.id, get_settings().crm_destination)
    assert row is not None
    return row


def fake_records(session: Session) -> list[FakeCrmRecord]:
    session.expire_all()
    return list(session.scalars(select(FakeCrmRecord).order_by(FakeCrmRecord.created_at)))


def attempts_of(session: Session, lead: CrmLead) -> list[CrmSyncAttempt]:
    session.expire_all()
    return list(
        session.scalars(
            select(CrmSyncAttempt)
            .where(CrmSyncAttempt.crm_lead_id == lead.id)
            .order_by(CrmSyncAttempt.id)
        )
    )


def run_due(session: Session, *, now: datetime) -> int:
    count = service.sync_due(session, now=now)
    session.commit()
    return count


# --- the human gate -------------------------------------------------------------------------------


NOT_APPROVED = [
    ReviewStatus.pending,
    ReviewStatus.rejected,
    ReviewStatus.not_a_fit,
    ReviewStatus.needs_enrichment,
    ReviewStatus.duplicate,
    ReviewStatus.do_not_contact,
]


@pytest.mark.parametrize("destination", ["csv", "airtable", "fake"])
@pytest.mark.parametrize("status", [*NOT_APPROVED, "suppressed", "undone"])
def test_nothing_but_an_approved_unsuppressed_business_ever_reaches_an_adapter(
    db: Session, business: Business, reviewer: User, destination: str, status: Any
) -> None:
    saved = adapters.snapshot()
    watcher = Spy()
    watcher.install(destination)
    settings = Settings(environment="ci", crm_destination=destination)  # type: ignore[arg-type]
    try:
        if status == "suppressed":
            row = make_opportunity(db, business, status=ReviewStatus.approved)
            row.decided_at = T0 - timedelta(days=1)
            compliance.suppress_business(
                db, business, reason="asked", source=SuppressionSource.admin, actor_id=reviewer.id
            )
        elif status == "undone":
            row = make_opportunity(db, business)
            db.commit()
            decision_id = approve(db, reviewer, row, now=T0)
            undo(db, reviewer, decision_id, now=T0 + timedelta(minutes=5))
        else:
            row = make_opportunity(db, business, status=status)
            row.decided_at = T0 - timedelta(days=1)
        db.commit()

        # However the lead row got there, the gate in front of the adapter refuses it.
        lead = service.get_lead(db, business.id, destination) or CrmLead(
            business_id=business.id,
            destination=destination,
            status=CrmLeadStatus.scheduled,
            due_at=T0,
        )
        db.add(lead)
        db.commit()
        service.sync_lead(db, lead, now=T0 + timedelta(days=2), settings=settings)
        db.commit()
        with pytest.raises(Exception, match="no approved opportunity or is suppressed"):
            service.sync_now(
                db, business.id, actor=reviewer, now=T0 + timedelta(days=2), settings=settings
            )
        service.sync_all(db, actor=reviewer, now=T0 + timedelta(days=2), settings=settings)
        db.commit()
    finally:
        adapters.restore(saved)

    assert watcher.calls == [], f"{destination} adapter was called for status {status}"
    db.expire_all()
    assert lead_for_destination(db, business, destination).status is CrmLeadStatus.cancelled
    assert fake_records(db) == []


def lead_for_destination(session: Session, business: Business, destination: str) -> CrmLead:
    row = service.get_lead(session, business.id, destination)
    assert row is not None
    return row


# --- the delay ------------------------------------------------------------------------------------


def test_an_approval_waits_for_the_undo_window_and_an_undone_one_never_leaves(
    db: Session, business: Business, reviewer: User, rep: User, spy: Spy
) -> None:
    kept = make_opportunity(db, business, "website_design")
    db.commit()
    approve(db, reviewer, kept, now=T0, rep=rep)
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled
    assert lead.due_at == T0 + DELAY

    assert run_due(db, now=T0 + DELAY - timedelta(seconds=1)) == 0
    assert spy.calls == [] and fake_records(db) == []

    assert run_due(db, now=T0 + DELAY) == 1
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.synced and lead.external_id is not None
    [record] = fake_records(db)
    assert record.fields[LABEL["services"]] == "Website redesign"
    assert record.fields[LABEL["assigned_rep"]] == "rep1@example.com"
    assert record.fields[LABEL["status"]] == "New"
    assert record.fields[LABEL["public_phone"]] == "(512) 555-0102"
    assert record.fields[LABEL["do_not_contact"]] is False
    assert record.fields[LABEL["radar_link"]] == f"http://localhost:3000/leads/{kept.id}"
    assert spy.methods() == ["find_by_keys", "upsert"]


def test_an_approval_undone_in_time_cancels_the_scheduled_sync(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    decision_id = approve(db, reviewer, row, now=T0)
    undo(db, reviewer, decision_id, now=T0 + timedelta(minutes=10))

    assert lead_for(db, business).status is CrmLeadStatus.cancelled
    assert run_due(db, now=T0 + timedelta(days=1)) == 0
    assert spy.calls == [] and fake_records(db) == []


def test_each_approval_waits_its_own_window(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    first = make_opportunity(db, business, "website_design")
    second = make_opportunity(db, business, "booking_setup", score=0.5)
    db.commit()
    approve(db, reviewer, first, now=T0)
    approve(db, reviewer, second, now=T0 + timedelta(minutes=20))

    run_due(db, now=T0 + DELAY)
    [record] = fake_records(db)
    assert record.fields[LABEL["services"]] == "Website redesign", "the second is still undoable"
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled
    assert lead.due_at == T0 + timedelta(minutes=20) + DELAY

    run_due(db, now=T0 + timedelta(minutes=20) + DELAY)
    [record] = fake_records(db)
    assert record.fields[LABEL["services"]] == "Website redesign; Online booking"
    assert lead_for(db, business).status is CrmLeadStatus.synced


def test_send_now_skips_the_delay_but_not_the_gate(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)

    read = service.sync_now(db, business.id, actor=reviewer, now=T0 + timedelta(seconds=1))
    db.commit()
    assert read.status is CrmLeadStatus.synced
    assert len(fake_records(db)) == 1

    other = make_business(db, name="Nobody Approved")
    make_opportunity(db, other)
    db.commit()
    with pytest.raises(Exception, match=r"not_eligible|no approved"):
        service.sync_now(db, other.id, actor=reviewer, now=T0)
    assert len(fake_records(db)) == 1


# --- one record per business, ownership -----------------------------------------------------------


def test_two_services_make_one_record_and_a_later_one_updates_it_without_touching_crm_fields(
    db: Session, business: Business, reviewer: User, rep: User, spy: Spy
) -> None:
    web = make_opportunity(db, business, "website_design", score=0.8)
    booking = make_opportunity(db, business, "booking_setup", score=0.6)
    seo = make_opportunity(db, business, "seo_gbp", score=0.9)
    db.commit()
    approve(db, reviewer, web, now=T0, rep=rep, note="Owner asked for a quote")
    approve(db, reviewer, booking, now=T0)
    run_due(db, now=T0 + DELAY)

    [record] = fake_records(db)
    assert record.fields[LABEL["services"]] == "Website redesign; Online booking"
    assert record.fields[LABEL["lead_score"]] == 80
    assert record.fields[LABEL["notes"]] == "Owner asked for a quote"
    assert (
        "Website redesign: Audit found something for website_design."
        in record.fields[LABEL["why_lead"]]
    )

    # A salesperson works the record in the CRM.
    record.fields = {
        **record.fields,
        LABEL["status"]: "Contacted",
        LABEL["assigned_rep"]: "someone-else@example.com",
        LABEL["follow_up_date"]: "2026-10-01",
        LABEL["notes"]: "Called, call back Monday",
    }
    db.commit()

    approve(db, reviewer, seo, now=T0 + timedelta(hours=1))
    run_due(db, now=T0 + timedelta(hours=1) + DELAY)

    [record] = fake_records(db)
    assert (
        record.fields[LABEL["services"]] == "SEO / Google profile; Website redesign; Online booking"
    )
    assert record.fields[LABEL["lead_score"]] == 90
    assert record.fields[LABEL["status"]] == "Contacted"
    assert record.fields[LABEL["assigned_rep"]] == "someone-else@example.com"
    assert record.fields[LABEL["follow_up_date"]] == "2026-10-01"
    assert record.fields[LABEL["notes"]] == "Called, call back Monday"
    lead = lead_for(db, business)
    carried = set(
        db.scalars(
            select(CrmLeadOpportunity.opportunity_id).where(
                CrmLeadOpportunity.crm_lead_id == lead.id
            )
        )
    )
    assert carried == {web.id, booking.id, seo.id}
    assert [a.action for a in attempts_of(db, lead)] == [
        CrmSyncAction.create,
        CrmSyncAction.update,
    ]
    assert int(db.scalar(select(func.count()).select_from(CrmLead)) or 0) == 1


# --- dedupe ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["radar_business_id", "website", "public_phone"])
def test_an_existing_record_is_linked_not_duplicated(
    db: Session, business: Business, reviewer: User, spy: Spy, key: str
) -> None:
    value = {
        "radar_business_id": str(business.id),
        "website": "https://www.bartoncreekplumbing.invalid/home",
        "public_phone": "(512) 555-0102",
    }[key]
    existing = FakeCrmRecord(
        fields={LABEL[key]: value, LABEL["status"]: "Contacted", LABEL["notes"]: "old"}
    )
    db.add(existing)
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    run_due(db, now=T0 + DELAY)

    records = fake_records(db)
    assert len(records) == 1 and records[0].id == existing.id
    assert records[0].fields[LABEL["services"]] == "Website redesign"
    assert records[0].fields[LABEL["status"]] == "Contacted", "linked, and CRM-owned fields kept"
    assert records[0].fields[LABEL["notes"]] == "old"
    lead = lead_for(db, business)
    assert lead.external_id == str(existing.id)
    assert [a.action for a in attempts_of(db, lead)] == [CrmSyncAction.link, CrmSyncAction.update]
    linked = [a for a in audit_rows(db) if a.action == "crm.link"]
    assert (
        linked
        and linked[0].after is not None
        and linked[0].after["external_id"] == str(existing.id)
    )


def audit_rows(session: Session) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


# --- unchanged ------------------------------------------------------------------------------------


def test_resyncing_an_unchanged_business_makes_no_call(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    run_due(db, now=T0 + DELAY)
    calls_after_first = len(spy.calls)

    service.sync_now(db, business.id, actor=reviewer, now=T0 + timedelta(hours=2))
    db.commit()

    assert len(spy.calls) == calls_after_first, "no adapter call for an unchanged payload"
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.synced
    assert [a.action for a in attempts_of(db, lead)] == [
        CrmSyncAction.create,
        CrmSyncAction.unchanged,
    ]


# --- retry and hold -------------------------------------------------------------------------------


def test_transient_failures_back_off_honour_retry_after_and_hold_after_three(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    spy.failures.extend(
        [
            CrmTransientError("429", http_status=429, retry_after_seconds=7),
            CrmTransientError("500", http_status=500),
            CrmTransientError("503", http_status=503),
        ]
    )
    due = T0 + DELAY

    run_due(db, now=due)
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled and lead.attempts == 1
    assert lead.due_at == due + timedelta(seconds=7), "Retry-After is honoured"

    run_due(db, now=due + timedelta(seconds=7))
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled and lead.attempts == 2
    assert lead.due_at == due + timedelta(seconds=7 + 120), "exponential backoff: 60 * 2^(2-1)"

    run_due(db, now=due + timedelta(seconds=200))
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.held and lead.attempts == 3
    assert lead.last_error is not None and "503" in lead.last_error
    rows = attempts_of(db, lead)
    assert [(a.status, a.http_status) for a in rows] == [
        (CrmSyncStatus.failed, 429),
        (CrmSyncStatus.failed, 500),
        (CrmSyncStatus.failed, 503),
    ]
    assert all(a.action is CrmSyncAction.create for a in rows)
    assert fake_records(db) == []

    # Retry from held works once the CRM is back.
    read = service.retry(db, lead.id, actor=reviewer, now=due + timedelta(hours=1))
    db.commit()
    assert read.status is CrmLeadStatus.synced
    assert len(fake_records(db)) == 1
    assert attempts_of(db, lead)[-1].status is CrmSyncStatus.ok


def test_an_auth_error_holds_immediately(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    spy.failures.append(CrmAuthError("bad token", http_status=401))

    run_due(db, now=T0 + DELAY)

    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.held and lead.attempts == 1
    [attempt] = attempts_of(db, lead)
    assert attempt.status is CrmSyncStatus.failed and attempt.http_status == 401
    assert run_due(db, now=T0 + timedelta(days=1)) == 0, "held leads are not retried on their own"
    later = T0 + timedelta(days=1)
    assert service.retry(db, lead.id, actor=reviewer, now=later).status is CrmLeadStatus.synced
    db.commit()
    with pytest.raises(Exception, match=r"not_held|Only a held lead"):
        service.retry(db, lead.id, actor=reviewer, now=later)


# --- undo after the sync --------------------------------------------------------------------------


def test_undoing_after_the_sync_removes_the_service_then_withdraws_only_a_new_record(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    web = make_opportunity(db, business, "website_design", score=0.8)
    booking = make_opportunity(db, business, "booking_setup", score=0.6)
    db.commit()
    web_decision = approve(db, reviewer, web, now=T0)
    booking_decision = approve(db, reviewer, booking, now=T0)
    # Sent early with Send now, so both approvals are still inside their undo window.
    service.sync_now(db, business.id, actor=reviewer, now=T0 + timedelta(minutes=1))
    db.commit()
    [record] = fake_records(db)
    assert record.fields[LABEL["services"]] == "Website redesign; Online booking"

    # An admin undoes one within the window: the record follows at once, no new wait.
    admin = make_user(db, Role.admin)
    undo(db, admin, booking_decision, now=T0 + timedelta(minutes=10))
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled and lead.due_at == T0 + timedelta(minutes=10)
    run_due(db, now=T0 + timedelta(minutes=10))
    [record] = fake_records(db)
    assert record.fields[LABEL["services"]] == "Website redesign"
    assert record.fields[LABEL["status"]] == "New"

    # A salesperson already moved it; withdrawing must not overwrite their status.
    record.fields = {**record.fields, LABEL["status"]: "Contacted"}
    db.commit()
    undo(db, admin, web_decision, now=T0 + timedelta(minutes=11))
    run_due(db, now=T0 + timedelta(minutes=11))
    [record] = fake_records(db)
    assert record.fields[LABEL["status"]] == "Contacted"
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.withdrawn
    assert attempts_of(db, lead)[-1].action is CrmSyncAction.withdraw
    assert len(fake_records(db)) == 1, "never deleted"


def test_withdrawing_sets_status_withdrawn_while_it_still_reads_new(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    web = make_opportunity(db, business)
    db.commit()
    decision = approve(db, reviewer, web, now=T0)
    service.sync_now(db, business.id, actor=reviewer, now=T0)
    db.commit()
    admin = make_user(db, Role.admin)
    undo(db, admin, decision, now=T0 + timedelta(minutes=5))
    run_due(db, now=T0 + timedelta(minutes=5))

    [record] = fake_records(db)
    assert record.fields[LABEL["status"]] == "Withdrawn"
    assert record.fields[LABEL["services"]] == "Website redesign", "radar fields are not blanked"
    assert lead_for(db, business).status is CrmLeadStatus.withdrawn


# --- suppression after the sync -------------------------------------------------------------------


def test_do_not_contact_after_the_sync_flags_the_record_and_lifting_clears_it(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    web = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, web, now=T0)
    run_due(db, now=T0 + DELAY)
    later = T0 + timedelta(hours=2)

    # A reviewer marks the business do-not-contact (from a fresh pending row).
    pending = make_opportunity(db, business, "seo_gbp")
    db.commit()
    decided = review.decide(
        db,
        pending.id,
        ReviewRequest(
            decision=Decision.do_not_contact, lock_version=0, note="Owner asked us to stop"
        ),
        actor=reviewer,
        now=later,
    )
    db.commit()
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled and lead.due_at == later
    run_due(db, now=later)

    [record] = fake_records(db)
    assert record.fields[LABEL["do_not_contact"]] is True
    assert record.fields[LABEL["services"]] == "Website redesign", "nothing else changed"
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.synced and lead.do_not_contact_sent is True
    assert spy.methods()[-1] == "mark_do_not_contact"
    assert attempts_of(db, lead)[-1].action is CrmSyncAction.mark_dnc

    # Undoing the do-not-contact lifts the suppression and restores the approval: flag off.
    undo(db, reviewer, decided.decision.id, now=later + timedelta(minutes=5))
    run_due(db, now=later + timedelta(minutes=5))
    [record] = fake_records(db)
    assert record.fields[LABEL["do_not_contact"]] is False
    assert lead_for(db, business).do_not_contact_sent is False
    assert len(fake_records(db)) == 1, "the record is never deleted"


def test_an_admin_suppression_reaches_a_synced_record_and_a_never_sent_lead_is_cancelled(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    admin = make_user(db, Role.admin)
    web = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, web, now=T0)
    run_due(db, now=T0 + DELAY)

    # Admin suppressions are stamped with the real clock; run the loop after that.
    far = max(T0, datetime.now(UTC)) + timedelta(days=1)
    suppression = compliance.add_suppression(
        db, SuppressionCreate(business_id=business.id, reason="client asked"), actor_id=admin.id
    )
    db.commit()
    run_due(db, now=far)
    assert fake_records(db)[0].fields[LABEL["do_not_contact"]] is True

    compliance.lift_suppression(db, suppression.id, actor_id=admin.id)
    db.commit()
    run_due(db, now=far + timedelta(hours=1))
    assert fake_records(db)[0].fields[LABEL["do_not_contact"]] is False

    other = make_business(db, name="Never Sent")
    make_audit(db, other)
    row = make_opportunity(db, other)
    db.commit()
    approve(db, reviewer, row, now=T0)
    compliance.add_suppression(
        db, SuppressionCreate(business_id=other.id, reason="client asked"), actor_id=admin.id
    )
    db.commit()
    assert lead_for(db, other).status is CrmLeadStatus.cancelled
    run_due(db, now=far + timedelta(days=1))
    assert len(fake_records(db)) == 1


# --- bookkeeping ----------------------------------------------------------------------------------


def test_every_attempt_writes_a_row_and_an_audit_entry(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    spy.failures.append(CrmTransientError("blip", http_status=502))
    run_due(db, now=T0 + DELAY)
    run_due(db, now=T0 + timedelta(hours=1))

    lead = lead_for(db, business)
    rows = attempts_of(db, lead)
    assert [(a.action, a.status) for a in rows] == [
        (CrmSyncAction.create, CrmSyncStatus.failed),
        (CrmSyncAction.create, CrmSyncStatus.ok),
    ]
    assert rows[0].error is not None and "blip" in rows[0].error
    assert all(a.duration_ms >= 0 for a in rows)
    actions = [a.action for a in audit_rows(db) if a.entity_type == "crm_lead"]
    assert actions == ["crm_lead.scheduled", "crm.create", "crm.create"]
    entry = next(a for a in audit_rows(db) if a.action == "crm.create")
    assert entry.entity_id == str(lead.id)
    assert entry.after is not None and entry.after["status"] == "failed"


def test_sync_all_sends_due_and_held_leads_and_pre_existing_approvals_but_not_fresh_ones(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    due_row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, due_row, now=T0)

    held_business = make_business(db, name="Held Co")
    held_business.phone_e164 = "+15125550111"
    make_audit(db, held_business)
    held_row = make_opportunity(db, held_business)
    db.commit()
    approve(db, reviewer, held_row, now=T0)
    spy.failures.append(CrmAuthError("nope", http_status=403))
    service.sync_now(db, held_business.id, actor=reviewer, now=T0)
    db.commit()
    assert lead_for(db, held_business).status is CrmLeadStatus.held

    # Approved before v0.7.0: no lead row at all. Old enough to be past its window.
    legacy = make_business(db, name="Legacy Co")
    legacy.phone_e164 = "+15125550122"
    make_audit(db, legacy)
    legacy_row = make_opportunity(db, legacy, status=ReviewStatus.approved)
    legacy_row.decided_at = T0 - timedelta(days=30)
    legacy_row.decided_by = reviewer.id

    fresh = make_business(db, name="Fresh Co")
    fresh.phone_e164 = "+15125550133"
    make_audit(db, fresh)
    fresh_row = make_opportunity(db, fresh)
    db.commit()
    approve(db, reviewer, fresh_row, now=T0 + DELAY)

    result = service.sync_all(db, actor=reviewer, now=T0 + DELAY)
    db.commit()

    assert result.considered == 3 and result.synced == 3, "the fresh approval is not due"
    assert {r.fields[LABEL["business_name"]] for r in fake_records(db)} == {
        "Barton Creek Plumbing",
        "Held Co",
        "Legacy Co",
    }
    assert lead_for(db, fresh).status is CrmLeadStatus.scheduled, "still inside its undo window"


def test_auto_sync_off_leaves_scheduled_leads_alone(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    settings = Settings(environment="ci", crm_destination="fake", crm_auto_sync=False)
    assert service.sync_due(db, now=T0 + timedelta(days=1), settings=settings) == 0
    assert lead_for(db, business).status is CrmLeadStatus.scheduled


def test_the_worker_entrypoints_run_in_their_own_transactions(
    db: Session, business: Business, reviewer: User, spy: Spy
) -> None:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0 - timedelta(days=1))
    assert sync_due() == 1
    assert lead_for(db, business).status is CrmLeadStatus.synced

    lead = lead_for(db, business)
    assert sync_crm_lead(lead.id) is CrmLeadStatus.synced
    assert len(fake_records(db)) == 1
