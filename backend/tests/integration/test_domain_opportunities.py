"""Domain findings in opportunities, the AI step, the CRM and the queue (v0.13.0, task 6)."""

import pytest
from sqlalchemy.orm import Session

from app.modules.audit_web.models import AuditStatus
from app.modules.auth.models import Role
from app.modules.crm.payload import approved_opportunities, build_record
from app.modules.opportunities import service
from app.modules.opportunities.models import ReviewStatus
from app.modules.review import service as review
from app.modules.review.models import Decision
from app.modules.review.schemas import ReviewRequest
from tests.conftest import make_user
from tests.factories import finding
from tests.integration.test_classification_run import (
    NOW,
    URL,
    classifications_of,
    make_audit,
    make_business,
    make_tools,
    opportunities_of,
)

EMAIL_CODES = (
    "multiple_spf_records",
    "spf_allows_all",
    "no_spf",
    "no_dmarc",
    "dmarc_policy_none",
    "no_domain_mx",
)
DOMAIN_SELLABLE = ("domain_expired", "domain_no_a_record", "domain_expiring_soon")
PAGE_CODES = ("no_online_booking", "no_https", "missing_title")


def classify(db: Session, business: object) -> service.ClassificationOutcome:
    outcome = service.classify(db, business, tools=make_tools("not json"), now=NOW)  # type: ignore[arg-type]
    db.flush()
    return outcome


@pytest.mark.parametrize("code", DOMAIN_SELLABLE)
def test_a_bot_challenge_audit_with_a_domain_finding_opens_website_design(
    db: Session, code: str
) -> None:
    """Acceptance 4: only the three domain codes contribute on `bot_challenge`."""
    business = make_business(db)
    make_audit(
        db,
        business,
        findings=[finding(code), *[finding(c, url=URL) for c in PAGE_CODES]],
        page_text=None,
        status=AuditStatus.bot_challenge,
    )

    outcome = classify(db, business)

    rows = opportunities_of(db, business)
    assert set(rows) == {"website_design"}
    assert service.cited_findings(rows["website_design"]) == {code}
    assert outcome.ai is None, "no page text: the AI step is never reached"
    assert classifications_of(db, business) == []


def test_a_bot_challenge_audit_without_domain_findings_still_opens_nothing(
    db: Session,
) -> None:
    business = make_business(db)
    make_audit(
        db,
        business,
        findings=[finding(c, url=URL) for c in (*PAGE_CODES, *EMAIL_CODES)],
        page_text=None,
        status=AuditStatus.bot_challenge,
    )

    classify(db, business)

    assert opportunities_of(db, business) == {}


@pytest.mark.parametrize("status", [AuditStatus.not_readable, AuditStatus.unreachable])
def test_unread_pages_let_the_three_domain_codes_through(db: Session, status: AuditStatus) -> None:
    business = make_business(db)
    make_audit(
        db,
        business,
        findings=[finding(c) for c in DOMAIN_SELLABLE] + [finding("no_online_booking")],
        page_text=None,
        status=status,
    )

    classify(db, business)

    rows = opportunities_of(db, business)
    assert set(rows) == {"website_design"}
    assert service.cited_findings(rows["website_design"]) == set(DOMAIN_SELLABLE)


def test_the_six_email_codes_open_nothing_reach_no_crm_and_stay_in_the_queue(
    db: Session,
) -> None:
    """Task 6: context for a rep, never something sold."""
    reviewer = make_user(db, Role.reviewer)
    business = make_business(db)
    email = [finding(c) for c in EMAIL_CODES]
    make_audit(db, business, findings=[*email, finding("no_online_booking", url=URL)])

    classify(db, business)

    rows = opportunities_of(db, business)
    assert set(rows) == {"booking_setup"}
    assert service.cited_findings(rows["booking_setup"]) == {"no_online_booking"}

    booking = rows["booking_setup"]
    review.decide(
        db,
        booking.id,
        ReviewRequest(decision=Decision.approve, lock_version=booking.lock_version),
        actor=reviewer,
        now=NOW,
    )
    assert booking.review_status is ReviewStatus.approved
    record = build_record(db, business, approved_opportunities(db, business.id))
    everything = " ".join(str(value) for value in record.fields.values())
    for label in ("SPF", "DMARC", "MX"):
        assert label not in everything
    assert "No online booking" in everything

    detail = review.review_detail(db, business.id, actor=reviewer, now=NOW)
    assert detail.audit is not None
    shown = {item["code"] for item in detail.audit.findings}
    assert set(EMAIL_CODES) <= shown


def test_the_crm_top_findings_are_unchanged_for_audits_with_no_unserviced_code(
    db: Session,
) -> None:
    """C5: before v0.13.0 the only service-null code was `robots_blocked`."""
    business = make_business(db)
    audit = make_audit(db, business, findings=[finding(c, url=URL) for c in PAGE_CODES])

    assert review.top_findings(audit, sellable_only=True) == review.top_findings(audit)
