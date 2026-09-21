"""Review decisions outrank classification: suppression, cool-down, never overwrite approved."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.modules.auth.models import Role, User
from app.modules.compliance.models import SuppressionSource
from app.modules.compliance.service import suppress_business
from app.modules.opportunities import service
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from app.modules.review import service as review
from app.modules.review.models import Decision
from app.modules.review.schemas import ReviewRequest
from tests.conftest import make_user
from tests.integration.test_classification_run import (
    NOW,
    make_audit,
    make_business,
    make_tools,
    opportunities_of,
)


def classify(session: Session, business: object, *, now: object = NOW) -> dict[str, Opportunity]:
    tools = make_tools("not json")
    service.classify(session, business, tools=tools, now=now)  # type: ignore[arg-type]
    session.flush()
    return opportunities_of(session, business)  # type: ignore[arg-type]


def decide(
    session: Session, opportunity: Opportunity, decision: Decision, actor: User, **fields: object
) -> None:
    review.decide(
        session,
        opportunity.id,
        ReviewRequest(decision=decision, lock_version=opportunity.lock_version, **fields),  # type: ignore[arg-type]
        actor=actor,
        now=NOW,
    )
    session.flush()


def test_a_suppressed_business_gets_no_pending_opportunity(db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    business = make_business(db)
    make_audit(db, business)
    assert set(classify(db, business)) == {"booking_setup"}
    [booking] = opportunities_of(db, business).values()
    decide(db, booking, Decision.do_not_contact, reviewer, note="asked")
    assert booking.review_status is ReviewStatus.do_not_contact

    outcome = service.classify(db, business, tools=make_tools("not json"), now=NOW)

    assert outcome.opportunities == [] and outcome.created == 0
    rows = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
    assert [r.review_status for r in rows] == [ReviewStatus.do_not_contact]


def test_an_admin_suppression_by_domain_catches_a_rediscovered_business(db: Session) -> None:
    admin = make_user(db, Role.admin)
    original = make_business(db, name="Original")
    suppress_business(
        db, original, reason="asked", source=SuppressionSource.admin, actor_id=admin.id
    )
    rediscovered = make_business(db, name="Same shop, new row")
    rediscovered.domain = original.domain
    make_audit(db, rediscovered)

    assert classify(db, rediscovered) == {}


def test_a_rejected_service_is_not_recreated_within_the_cooldown(db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    business = make_business(db)
    make_audit(db, business)
    [booking] = classify(db, business).values()
    decide(db, booking, Decision.reject, reviewer, reason_code="ai_mistake")

    within = classify(db, business, now=NOW + timedelta(days=89))
    assert within == {"booking_setup": booking}, "the rejected row is left alone"
    assert booking.review_status is ReviewStatus.rejected

    classify(db, business, now=NOW + timedelta(days=91))
    rows = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
    assert sorted(r.review_status.value for r in rows) == ["pending", "rejected"]


def test_not_a_fit_and_duplicate_also_cool_down(db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    cases: list[tuple[Decision, dict[str, object]]] = [
        (Decision.not_a_fit, {"reason_code": "too_small"}),
        (Decision.duplicate, {}),
    ]
    for decision, fields in cases:
        business = make_business(db)
        make_audit(db, business)
        [booking] = classify(db, business).values()
        if decision is Decision.duplicate:
            other_business = make_business(db)
            other = Opportunity(
                business_id=other_business.id,
                service="booking_setup",
                source=booking.source,
                reason="",
                evidence=[],
                confidence=booking.confidence,
                score=booking.score,
                score_components={},
                scoring_version="scoring-1",
            )
            db.add(other)
            db.flush()
            fields = {"duplicate_of": other.id}
        decide(db, booking, decision, reviewer, **fields)

        rows = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
        assert len(rows) == 1
        classify(db, business, now=NOW + timedelta(days=1))
        rows = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
        assert len(rows) == 1, f"{decision.value} must not be re-created as pending"


def test_an_approved_opportunity_is_never_overwritten(db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    business = make_business(db)
    make_audit(db, business)
    [booking] = classify(db, business).values()
    decide(db, booking, Decision.approve, reviewer, note="yes")
    original = (booking.reason, booking.evidence, booking.confidence, booking.updated_at)

    classify(db, business, now=NOW + timedelta(days=400))

    db.refresh(booking)
    assert booking.review_status is ReviewStatus.approved
    assert (booking.reason, booking.evidence, booking.confidence, booking.updated_at) == original
    rows = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
    assert len(rows) == 1, "no second pending row is opened beside an approved one"


def test_a_needs_enrichment_row_is_refreshed_in_place(db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    business = make_business(db)
    make_audit(db, business)
    [booking] = classify(db, business).values()
    decide(db, booking, Decision.needs_enrichment, reviewer, note="check")

    outcome = service.classify(db, business, tools=make_tools("not json"), now=NOW)

    assert outcome.updated == 1 and outcome.created == 0
    db.refresh(booking)
    assert booking.review_status is ReviewStatus.needs_enrichment
    rows = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
    assert len(rows) == 1


# --- a lost race must not cost the business its classification (spec v0.9.0) ------------


def test_an_opportunity_another_writer_opened_first_is_updated_not_duplicated(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`uq_opportunities_pending_business_service` is a partial unique index, not a hint.

    In the v0.9.0 incident a `pending` row that was already there came back from the
    flush as `duplicate key value violates unique constraint
    "uq_opportunities_pending_business_service"`, which aborted the transaction and cost
    the business its whole classification ("classifying one business failed" in
    `logs/worker.log`). Reproduced in the one window where it can happen: a second
    session commits the row after `pending_opportunities` has read and before the insert
    goes in, which is exactly the shape of a lost race.
    """
    business = make_business(db)
    make_audit(db, business)
    db.commit()

    real_read = service.pending_opportunities
    intruder: dict[str, uuid.UUID] = {}

    def read_then_let_somebody_else_in(
        session: Session, business_id: uuid.UUID
    ) -> dict[str, Opportunity]:
        found = real_read(session, business_id)
        if not intruder:
            with session_scope() as other:
                row = Opportunity(
                    business_id=business_id,
                    service="booking_setup",
                    review_status=ReviewStatus.pending,
                    source=OpportunitySource.rules,
                    reason="opened by somebody else",
                    evidence=[],
                    confidence=Decimal("0.1"),
                    score=Decimal("1"),
                    score_components={},
                    scoring_version="rival",
                )
                other.add(row)
                other.flush()
                intruder["id"] = row.id
        return found

    monkeypatch.setattr(service, "pending_opportunities", read_then_let_somebody_else_in)
    outcome = service.classify(db, business, tools=make_tools("not json"), now=NOW)
    monkeypatch.undo()
    db.commit()

    assert intruder, "the test never ran its rival insert, so it proved nothing"
    assert outcome.created == 0 and outcome.updated == 1, "the row was taken over, not re-created"
    rows = list(
        db.scalars(
            select(Opportunity).where(
                Opportunity.business_id == business.id,
                Opportunity.service == "booking_setup",
            )
        )
    )
    assert len(rows) == 1, "one open row per business and service, still"
    assert rows[0].id == intruder["id"], "the row that won the race is the one we updated"
    assert rows[0].scoring_version != "rival", "and it carries this classification's values"
    assert rows[0].reason != "opened by somebody else"


def test_a_pending_row_is_preferred_over_a_newer_needs_enrichment_one(db: Session) -> None:
    """The unique index covers `pending`, so `pending` is the row an upsert must update."""
    reviewer = make_user(db, Role.reviewer)
    business = make_business(db)
    make_audit(db, business)
    [booking] = classify(db, business).values()
    decide(db, booking, Decision.needs_enrichment, reviewer, note="check")
    db.flush()

    # A second, later row for the same service, still `pending`: only one may ever be.
    later = Opportunity(
        business_id=business.id,
        service="booking_setup",
        review_status=ReviewStatus.pending,
        source=OpportunitySource.rules,
        reason="the open one",
        evidence=[],
        confidence=Decimal("0.2"),
        score=Decimal("2"),
        score_components={},
        scoring_version="older",
    )
    db.add(later)
    db.commit()

    assert service.pending_opportunities(db, business.id)["booking_setup"].id == later.id
