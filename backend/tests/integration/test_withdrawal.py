"""v0.12.1: a pending opportunity the latest audit no longer supports is withdrawn."""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import InvalidStateTransitionError
from app.modules.audit_web.models import AuditStatus
from app.modules.auth.models import Role
from app.modules.businesses.models import Business
from app.modules.crm.payload import approved_opportunities
from app.modules.normalization.schemas import BusinessStatus
from app.modules.opportunities import service
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from app.modules.review import service as review
from app.modules.review.models import Decision
from app.modules.review.schemas import ReviewRequest
from tests.conftest import make_user
from tests.factories import check, finding
from tests.integration.test_classification_run import (
    NOW,
    URL,
    make_audit,
    make_business,
    make_tools,
    opportunities_of,
)

LATER = NOW + timedelta(days=1)


def classify(session: Session, business: Business, *, now: object = NOW) -> None:
    service.classify(session, business, tools=make_tools("not json"), now=now)  # type: ignore[arg-type]
    session.flush()


def rows_of(session: Session, business: Business) -> list[Opportunity]:
    return list(
        session.scalars(
            select(Opportunity)
            .where(Opportunity.business_id == business.id)
            .order_by(Opportunity.created_at, Opportunity.id)
        )
    )


def no_social_audit(session: Session, business: Business, *, findings: list[object]) -> object:
    """An audit that also yields `ads_social`: parsed, and no social profile links."""
    audit = make_audit(session, business, findings=findings, created_at=LATER)  # type: ignore[arg-type]
    audit.checks = {"parsed": check(True), "social_links": check([])}
    session.flush()
    return audit


def test_an_audit_that_stops_supporting_a_service_withdraws_it_with_a_reason(
    db: Session,
) -> None:
    business = make_business(db)
    make_audit(db, business)
    classify(db, business)
    [booking] = opportunities_of(db, business).values()
    assert booking.withdrawn_at is None

    later = make_audit(db, business, findings=[], created_at=LATER)
    outcome = service.classify(db, business, tools=make_tools("not json"), now=LATER)

    assert outcome.withdrawn == 1
    assert booking.review_status is ReviewStatus.pending, "withdrawal is not a decision"
    assert booking.withdrawn_at == LATER
    reason = booking.withdrawn_reason or ""
    assert "service_absent" in reason and "findings_absent" in reason
    assert "no_online_booking" in reason
    assert str(later.id) in reason


# An audit that could not look is not evidence that anything is gone (v0.12.1, correction H).
# Production, 2026-10-01 11:56: a `failed` audit (our own DNS outage) withdrew all four of
# ATX Electrical's pending rows as `findings_absent`.
@pytest.mark.parametrize(
    "status",
    [
        AuditStatus.failed,
        AuditStatus.unreachable,
        AuditStatus.bot_challenge,
        AuditStatus.not_readable,
        AuditStatus.robots_blocked,
    ],
)
def test_an_audit_that_could_not_look_withdraws_nothing(db: Session, status: AuditStatus) -> None:
    business = make_business(db)
    make_audit(db, business)
    classify(db, business)
    [booking] = opportunities_of(db, business).values()

    make_audit(db, business, findings=[], page_text=None, status=status, created_at=LATER)
    outcome = service.classify(db, business, tools=make_tools("not json"), now=LATER)

    assert outcome.withdrawn == 0
    assert booking.withdrawn_at is None and booking.withdrawn_reason is None
    assert booking.review_status is ReviewStatus.pending


def test_a_closed_business_still_has_its_rows_withdrawn_whatever_its_audit_says(
    db: Session,
) -> None:
    """The business itself is gone, so nothing it was offered still stands."""
    business = make_business(db)
    make_audit(db, business)
    classify(db, business)
    [booking] = opportunities_of(db, business).values()

    business.business_status = BusinessStatus.closed_permanently
    make_audit(
        db, business, findings=[], page_text=None, status=AuditStatus.failed, created_at=LATER
    )
    outcome = service.classify(db, business, tools=make_tools("not json"), now=LATER)

    assert outcome.withdrawn == 1
    assert booking.withdrawn_at == LATER
    assert "service_absent" in (booking.withdrawn_reason or "")


def test_a_row_that_cites_no_findings_is_never_withdrawn_by_the_findings_test(
    db: Session,
) -> None:
    """Correction C: `ads_social` rests on an empty `social_links`, not on a finding."""
    business = make_business(db)
    no_social_audit(db, business, findings=[finding("no_online_booking", url=URL)])
    classify(db, business, now=LATER)
    ads = opportunities_of(db, business)["ads_social"]
    assert service.cited_findings(ads) == set()

    # A newer audit with no findings at all: the booking row goes, the ads row stays,
    # because the audit still produces its service and it cites nothing to be absent.
    newest = make_audit(db, business, findings=[], created_at=LATER + timedelta(days=1))
    newest.checks = {"parsed": check(True), "social_links": check([])}
    db.flush()
    classify(db, business, now=LATER + timedelta(days=1))

    rows = opportunities_of(db, business)
    assert rows["booking_setup"].withdrawn_at is not None
    assert rows["ads_social"].withdrawn_at is None
    # And with no merged result to compare with (the backfill's view), still nothing.
    assert (
        service.withdrawal_reasons(ads, audit=newest, merged_services=None, merged_complete=False)
        == []
    )


def test_an_approved_opportunity_is_never_withdrawn_and_is_logged(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    reviewer = make_user(db, Role.reviewer)
    business = make_business(db)
    make_audit(db, business)
    classify(db, business)
    [booking] = opportunities_of(db, business).values()
    review.decide(
        db,
        booking.id,
        ReviewRequest(decision=Decision.approve, lock_version=booking.lock_version),
        actor=reviewer,
        now=NOW,
    )
    db.flush()

    make_audit(db, business, findings=[], created_at=LATER)
    with caplog.at_level(logging.WARNING, logger="app.opportunities"):
        classify(db, business, now=LATER)

    assert booking.review_status is ReviewStatus.approved
    assert booking.withdrawn_at is None
    warned = [r for r in caplog.records if "not withdrawn" in r.getMessage()]
    assert warned and warned[0].levelno == logging.WARNING
    assert getattr(warned[0], "service", None) == "booking_setup"
    assert "findings_absent" in str(getattr(warned[0], "reason", ""))


def test_a_later_supporting_audit_clears_the_withdrawal_and_reuses_the_row(
    db: Session,
) -> None:
    business = make_business(db)
    make_audit(db, business)
    classify(db, business)
    [booking] = opportunities_of(db, business).values()
    make_audit(db, business, findings=[], created_at=LATER)
    classify(db, business, now=LATER)
    assert booking.withdrawn_at is not None

    back = LATER + timedelta(days=1)
    make_audit(db, business, created_at=back)
    outcome = service.classify(db, business, tools=make_tools("not json"), now=back)

    assert [r.id for r in rows_of(db, business)] == [booking.id], "no second row"
    assert outcome.created == 0 and outcome.updated == 1
    assert booking.withdrawn_at is None and booking.withdrawn_reason is None


def test_an_ai_only_row_is_not_withdrawn_because_the_ai_did_not_answer(db: Session) -> None:
    business = make_business(db)
    audit = make_audit(db, business)
    row = Opportunity(
        business_id=business.id,
        service="website_redesign",
        source=OpportunitySource.ai,
        reason="The AI said so.",
        evidence=[{"finding_code": "no_online_booking", "text": "x", "source": "ai"}],
        confidence=Decimal("0.5"),
        score=Decimal("0.5"),
        score_components={},
        scoring_version="scoring-1",
    )
    db.add(row)
    db.flush()

    classify(db, business)

    assert row.withdrawn_at is None, "its cited finding is still there; the AI just failed"
    assert "no_online_booking" in audit.finding_codes


def test_a_new_pending_row_can_open_beside_a_withdrawn_one(db: Session) -> None:
    """The index change: a withdrawn row no longer holds the one pending slot."""
    business = make_business(db)

    def pending(withdrawn: bool) -> Opportunity:
        return Opportunity(
            business_id=business.id,
            service="booking_setup",
            source=OpportunitySource.rules,
            reason="",
            evidence=[],
            confidence=Decimal("0.5"),
            score=Decimal("0.5"),
            score_components={},
            scoring_version="scoring-1",
            withdrawn_at=NOW if withdrawn else None,
            withdrawn_reason="service_absent: test" if withdrawn else None,
        )

    db.add(pending(True))
    db.flush()
    live = pending(False)
    db.add(live)
    db.flush()
    assert service.pending_opportunities(db, business.id)["booking_setup"].id == live.id

    savepoint = db.begin_nested()
    db.add(pending(False))
    with pytest.raises(IntegrityError, match=service.PENDING_UNIQUE_INDEX):
        db.flush()
    savepoint.rollback()


def _withdrawn_pending(db: Session) -> tuple[Business, Opportunity, Opportunity]:
    business = make_business(db, name=f"Queue {uuid.uuid4().hex[:6]}")
    make_audit(db, business)

    def row(service_key: str, confidence: str, *, withdrawn: bool) -> Opportunity:
        opportunity = Opportunity(
            business_id=business.id,
            service=service_key,
            source=OpportunitySource.rules,
            reason="",
            evidence=[],
            confidence=Decimal(confidence),
            score=Decimal("0.9") if withdrawn else Decimal("0.4"),
            score_components={},
            scoring_version="scoring-1",
            withdrawn_at=NOW if withdrawn else None,
            withdrawn_reason="findings_absent: test" if withdrawn else None,
        )
        db.add(opportunity)
        return opportunity

    live = row("booking_setup", "0.9", withdrawn=False)
    gone = row("website_redesign", "0.9", withdrawn=True)
    row("ai_chat_setup", "0.1", withdrawn=True)  # weak and withdrawn: not in weak_hidden
    db.flush()
    return business, live, gone


def test_a_withdrawn_row_is_absent_from_the_queue_and_its_counts(db: Session) -> None:
    business, live, gone = _withdrawn_pending(db)
    only_withdrawn = make_business(db, name="Only withdrawn")
    make_audit(db, only_withdrawn)
    db.add(
        Opportunity(
            business_id=only_withdrawn.id,
            service="booking_setup",
            source=OpportunitySource.rules,
            reason="",
            evidence=[],
            confidence=Decimal("0.9"),
            score=Decimal("0.99"),
            score_components={},
            scoring_version="scoring-1",
            withdrawn_at=NOW,
            withdrawn_reason="findings_absent: test",
        )
    )
    db.flush()

    page = review.review_queue(db, limit=100)
    items = {item.business_id: item for item in page.items}

    assert only_withdrawn.id not in items, "a business with only withdrawn rows is not queued"
    item = items[business.id]
    assert [o.id for o in item.opportunities] == [live.id]
    assert item.top_score == pytest.approx(0.4), "the withdrawn row's 0.9 does not rank it"
    assert item.weak_hidden == 0, "a withdrawn weak row is not counted as hidden"

    listed = service.list_opportunities(db, business_id=business.id, limit=100)
    assert [o.id for o in listed.items] == [live.id]
    both = service.list_opportunities(
        db, business_id=business.id, include_withdrawn=True, limit=100
    )
    assert gone.id in {o.id for o in both.items}


def test_keyset_paging_skips_withdrawn_rows(db: Session) -> None:
    for _ in range(3):
        _withdrawn_pending(db)
    seen: list[uuid.UUID] = []
    cursor = None
    while True:
        page = review.review_queue(db, limit=1, cursor=cursor)
        seen.extend(item.business_id for item in page.items)
        for item in page.items:
            assert all(o.score == pytest.approx(0.4) for o in item.opportunities)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 3


def test_a_withdrawn_row_cannot_be_approved_and_never_reaches_the_crm(db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    business, _, gone = _withdrawn_pending(db)

    with pytest.raises(InvalidStateTransitionError, match="withdrawn"):
        review.decide(
            db,
            gone.id,
            ReviewRequest(decision=Decision.approve, lock_version=gone.lock_version),
            actor=reviewer,
            now=NOW,
        )

    # Even an approved row that somehow carries a withdrawal stays out of the payload.
    gone.review_status = ReviewStatus.approved
    gone.decided_at = NOW
    db.flush()
    assert gone.id not in {o.id for o in approved_opportunities(db, business.id)}
