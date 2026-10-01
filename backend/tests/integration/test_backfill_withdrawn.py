"""v0.12.1 backfill: lists the orphans, writes nothing without `--apply`."""

import importlib.util
import pathlib
from decimal import Decimal
from types import ModuleType
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit_web.models import AuditStatus
from app.modules.opportunities.backfill import find_orphans
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from tests.factories import finding
from tests.integration.test_classification_run import NOW, URL, make_audit, make_business

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "backfill_withdrawn.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_withdrawn", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def opportunity(
    business_id: Any,
    service: str,
    evidence: list[dict[str, Any]],
    status: ReviewStatus = ReviewStatus.pending,
) -> Opportunity:
    return Opportunity(
        business_id=business_id,
        service=service,
        source=OpportunitySource.rules,
        reason="",
        evidence=evidence,
        confidence=Decimal("0.5"),
        score=Decimal("0.5"),
        score_components={},
        scoring_version="scoring-1",
        review_status=status,
    )


def seed(db: Session, status: AuditStatus = AuditStatus.done) -> dict[str, Opportunity]:
    business = make_business(db)
    # The latest audit has only `no_https`; `no_online_booking` is gone.
    make_audit(db, business, findings=[finding("no_https", url=URL)], status=status, created_at=NOW)
    rows = {
        "orphan": opportunity(
            business.id, "booking_setup", [{"finding_code": "no_online_booking", "text": "x"}]
        ),
        "supported": opportunity(
            business.id, "website_redesign", [{"finding_code": "no_https", "text": "x"}]
        ),
        # Correction C: cites nothing, so "all cited findings absent" must not hold.
        "ads": opportunity(business.id, "ads_social", [{"finding_code": None, "text": "x"}]),
        "approved": opportunity(
            business.id,
            "ai_chat_setup",
            [{"finding_code": "no_live_chat", "text": "x"}],
            ReviewStatus.approved,
        ),
    }
    db.add_all(rows.values())
    db.flush()
    return rows


def test_the_dry_run_lists_the_orphans_and_writes_nothing(db: Session) -> None:
    rows = seed(db)

    found = find_orphans(db, apply=False, now=NOW)

    by_id = {o.opportunity_id: o for o in found}
    assert set(by_id) == {str(rows["orphan"].id), str(rows["approved"].id)}
    assert by_id[str(rows["orphan"].id)].withdrawn is False
    assert all(row.withdrawn_at is None for row in rows.values())
    lines = load_script().render(found, apply=False)
    assert any(line.startswith("would withdraw\t") and "booking_setup" in line for line in lines)
    assert any(line.startswith("kept (approved)\t") for line in lines)
    assert lines[-1].startswith("1 pending to withdraw (dry run")


def test_apply_withdraws_only_the_pending_orphan(db: Session) -> None:
    rows = seed(db)

    found = find_orphans(db, apply=True, now=NOW)

    assert rows["orphan"].withdrawn_at == NOW
    assert "findings_absent" in (rows["orphan"].withdrawn_reason or "")
    assert rows["approved"].withdrawn_at is None
    assert rows["approved"].review_status is ReviewStatus.approved
    assert rows["ads"].withdrawn_at is None
    assert rows["supported"].withdrawn_at is None
    assert load_script().render(found, apply=True)[-1].startswith("1 pending withdrawn")
    # Idempotent: a second run finds the pending orphan already withdrawn.
    assert [o.review_status for o in find_orphans(db, apply=True, now=NOW)] == ["approved"]


def test_a_business_whose_latest_audit_could_not_look_is_skipped_and_counted(
    db: Session,
) -> None:
    """Correction H: only a `done` or `skipped` latest audit can show a finding is gone."""
    rows = seed(db, status=AuditStatus.failed)

    found = find_orphans(db, apply=True, now=NOW)

    assert all(row.withdrawn_at is None for row in rows.values())
    assert {o.opportunity_id for o in found} == {str(rows["orphan"].id), str(rows["approved"].id)}
    assert all(o.skipped_audit_status == "failed" and not o.withdrawn for o in found)
    lines = load_script().render(found, apply=False)
    assert any(line.startswith("skipped (audit failed)\t") for line in lines)
    assert lines[-1] == (
        "0 pending to withdraw (dry run; pass --apply to write); 0 not pending, kept and "
        "logged; 2 skipped: latest audit is not done or skipped"
    )
