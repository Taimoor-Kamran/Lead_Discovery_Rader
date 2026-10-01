"""v0.12.1, correction H: restore rows withdrawn against an audit that could not look."""

import importlib.util
import pathlib
from datetime import timedelta
from types import ModuleType

from sqlalchemy.orm import Session

from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.normalization.schemas import BusinessStatus
from app.modules.opportunities.backfill import find_wrongly_withdrawn
from app.modules.opportunities.models import Opportunity
from tests.integration.test_backfill_withdrawn import opportunity
from tests.integration.test_classification_run import NOW, make_audit, make_business

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "repair_wrongly_withdrawn.py"
CITES = [{"finding_code": "no_online_booking", "text": "x"}]


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("repair_wrongly_withdrawn", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def withdrawn(db: Session, business: Business, audit: WebsiteAudit, service: str) -> Opportunity:
    """A pending row withdrawn the way production did it, naming `audit`."""
    row = opportunity(business.id, service, CITES)
    row.withdrawn_at = NOW
    row.withdrawn_reason = (
        f"service_absent: audit {audit.id} no longer produces this service; "
        f"findings_absent: none of the cited findings (no_online_booking) is in audit {audit.id}"
    )
    db.add(row)
    db.flush()
    return row


def test_dry_run_lists_rows_withdrawn_on_a_failed_audit_and_writes_nothing(db: Session) -> None:
    business = make_business(db)
    failed = make_audit(db, business, findings=[], page_text=None, status=AuditStatus.failed)
    row = withdrawn(db, business, failed, "booking_setup")

    found = find_wrongly_withdrawn(db, apply=False)

    assert [(r.opportunity_id, r.held, r.restored) for r in found] == [(str(row.id), None, False)]
    assert found[0].audit_statuses == ("failed",)
    assert row.withdrawn_at is not None
    lines = load_script().render(found, apply=False)
    assert lines[0].startswith("would restore\t")
    assert lines[-1] == "1 to restore (dry run; pass --apply to write); 0 held"


def test_apply_restores_only_rows_withdrawn_against_an_audit_that_could_not_look(
    db: Session,
) -> None:
    business = make_business(db)
    unreachable = make_audit(
        db, business, findings=[], page_text=None, status=AuditStatus.unreachable
    )
    wrong = withdrawn(db, business, unreachable, "booking_setup")
    other = make_business(db, name="Other")
    done = make_audit(db, other, findings=[], created_at=NOW + timedelta(days=1))
    right = withdrawn(db, other, done, "booking_setup")
    closed = make_business(db, name="Closed")
    closed.business_status = BusinessStatus.closed_permanently
    closed_audit = make_audit(db, closed, findings=[], page_text=None, status=AuditStatus.failed)
    gone = withdrawn(db, closed, closed_audit, "booking_setup")

    found = find_wrongly_withdrawn(db, apply=True)

    assert wrong.withdrawn_at is None and wrong.withdrawn_reason is None
    assert right.withdrawn_at is not None, "a done audit's withdrawal stands"
    assert gone.withdrawn_at is not None, "a closed business's withdrawal stands"
    held = {r.opportunity_id: r.held for r in found}
    assert held == {str(wrong.id): None, str(gone.id): "business is permanently closed"}
    # Idempotent: the restored row is no longer withdrawn, so it is not listed again.
    assert [r.opportunity_id for r in find_wrongly_withdrawn(db, apply=True)] == [str(gone.id)]


def test_a_row_is_held_when_a_live_pending_row_for_its_service_already_exists(
    db: Session,
) -> None:
    """The unique index allows one live pending row per business and service."""
    business = make_business(db)
    failed = make_audit(db, business, findings=[], page_text=None, status=AuditStatus.failed)
    first = withdrawn(db, business, failed, "booking_setup")
    second = withdrawn(db, business, failed, "booking_setup")

    found = find_wrongly_withdrawn(db, apply=True)

    assert sorted(row.withdrawn_at is None for row in (first, second)) == [False, True]
    assert sorted((r.held or "") for r in found) == [
        "",
        "a live pending row for this service already exists",
    ]
