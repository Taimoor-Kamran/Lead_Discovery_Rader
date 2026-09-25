"""When a business is due for another audit, by what its newest audit says (spec v0.11.1).

Before v0.11.1 any audit younger than AUDIT_MAX_AGE_DAYS counted as fresh, so a single
`failed` audit — a timeout on our side — hid a business from every run for a month.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.audit_web.models import AuditStatus
from app.modules.audit_web.service import needs_audit
from app.modules.businesses.models import Business
from tests.factories import make_audit
from tests.integration.test_website_audits_api import make_business

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def add_audits(db: Session, business: Business, *history: tuple[AuditStatus, timedelta]) -> None:
    """Audits oldest first, each `age` before NOW."""
    for status, age in history:
        audit = make_audit(business, status=status)
        audit.created_at = NOW - age
        db.add(audit)
    db.flush()


@pytest.fixture
def business(db: Session) -> Business:
    return make_business(db)


def test_a_business_never_audited_is_due(db: Session, business: Business) -> None:
    assert needs_audit(db, business, now=NOW)


# --- failed -------------------------------------------------------------------------------


def test_a_failed_audit_is_due_again_after_the_retry_window(
    db: Session, business: Business
) -> None:
    add_audits(db, business, (AuditStatus.failed, timedelta(hours=6, minutes=1)))
    assert needs_audit(db, business, now=NOW)


def test_a_failed_audit_is_not_due_inside_the_retry_window(db: Session, business: Business) -> None:
    add_audits(db, business, (AuditStatus.failed, timedelta(hours=5, minutes=59)))
    assert not needs_audit(db, business, now=NOW)


def test_a_failed_audit_after_a_good_one_is_still_due_after_the_retry_window(
    db: Session, business: Business
) -> None:
    """Hoffman and Harlow: an older good audit must not keep the new failure hidden."""
    add_audits(
        db,
        business,
        (AuditStatus.done, timedelta(days=2)),
        (AuditStatus.failed, timedelta(hours=7)),
    )
    assert needs_audit(db, business, now=NOW)


def test_the_retry_window_comes_from_settings(
    db: Session, business: Business, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_FAILED_RETRY_HOURS", "24")
    get_settings.cache_clear()
    add_audits(db, business, (AuditStatus.failed, timedelta(hours=7)))
    assert not needs_audit(db, business, now=NOW)


# --- successful ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [AuditStatus.done, AuditStatus.skipped, AuditStatus.robots_blocked]
)
def test_a_settled_audit_is_not_due_inside_30_days(
    db: Session, business: Business, status: AuditStatus
) -> None:
    add_audits(db, business, (status, timedelta(days=29, hours=23)))
    assert not needs_audit(db, business, now=NOW)


@pytest.mark.parametrize(
    "status", [AuditStatus.done, AuditStatus.skipped, AuditStatus.robots_blocked]
)
def test_a_settled_audit_is_due_after_30_days(
    db: Session, business: Business, status: AuditStatus
) -> None:
    add_audits(db, business, (status, timedelta(days=30, minutes=1)))
    assert needs_audit(db, business, now=NOW)


def test_a_good_audit_after_a_failure_gets_the_full_30_days(
    db: Session, business: Business
) -> None:
    add_audits(
        db,
        business,
        (AuditStatus.failed, timedelta(days=3)),
        (AuditStatus.done, timedelta(days=2)),
    )
    assert not needs_audit(db, business, now=NOW)


# --- unreachable --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("streak", "wait_days"),
    [(1, 1), (2, 3), (3, 7), (4, 30), (6, 30)],
)
def test_an_unreachable_site_backs_off_one_step_per_consecutive_unreachable_audit(
    db: Session, business: Business, streak: int, wait_days: int
) -> None:
    older = [(AuditStatus.unreachable, timedelta(days=60 + i)) for i in range(streak - 1, 0, -1)]
    add_audits(db, business, *older, (AuditStatus.unreachable, timedelta(days=wait_days, hours=-1)))
    assert not needs_audit(db, business, now=NOW)

    later = NOW + timedelta(hours=2)
    assert needs_audit(db, business, now=later)


def test_a_reachable_audit_resets_the_unreachable_streak(db: Session, business: Business) -> None:
    add_audits(
        db,
        business,
        (AuditStatus.unreachable, timedelta(days=90)),
        (AuditStatus.unreachable, timedelta(days=80)),
        (AuditStatus.done, timedelta(days=40)),
        (AuditStatus.unreachable, timedelta(days=1, minutes=1)),
    )
    assert needs_audit(db, business, now=NOW)


def test_a_failed_audit_does_not_count_toward_the_unreachable_streak(
    db: Session, business: Business
) -> None:
    add_audits(
        db,
        business,
        (AuditStatus.unreachable, timedelta(days=20)),
        (AuditStatus.failed, timedelta(days=10)),
        (AuditStatus.unreachable, timedelta(days=1, minutes=1)),
    )
    assert needs_audit(db, business, now=NOW)


def test_the_backoff_comes_from_settings(
    db: Session, business: Business, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_UNREACHABLE_BACKOFF_DAYS", "2, 5")
    get_settings.cache_clear()
    add_audits(db, business, (AuditStatus.unreachable, timedelta(days=1, hours=12)))
    assert not needs_audit(db, business, now=NOW)
