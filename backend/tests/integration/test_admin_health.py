"""`/admin/health` (spec v0.8.0 §4): every metric in the table, every alert rule, acknowledge."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.ai.budget import AIBudget
from app.modules.alerts import service as alerts
from app.modules.alerts.models import Alert
from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.crm.models import CrmLead, CrmLeadStatus
from app.modules.discovery.models import ApiCall
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import DISCOVERY_JOB_KIND, run_inline
from app.modules.monitoring import service as monitoring
from app.modules.sources.models import Source, SourceKind
from tests.conftest import auth_headers, make_user

API = "/api/v1"

METRIC_BLOCKS = {
    "jobs",
    "sources",
    "timings",
    "queue",
    "ai",
    "data_quality",
    "duplicates",
    "crm",
    "freshness",
    "audits",
    "backups",
    "thresholds",
    "alerts",
}


def _finished_run(db: Session, kind: str, status: JobRunStatus, *, seconds: float = 10.0) -> JobRun:
    now = datetime.now(UTC)
    run = JobRun(
        kind=kind,
        status=status,
        started_at=now - timedelta(seconds=seconds),
        finished_at=now,
        attempts=1,
    )
    db.add(run)
    db.commit()
    return run


def _tech_admin(db: Session) -> User:
    return make_user(db, Role.tech_admin)


# --- access and shape --------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.reviewer, Role.sales_rep, Role.crm_manager])
def test_only_admins_and_tech_admins_read_the_health_page(
    client: TestClient, db: Session, role: Role
) -> None:
    user = make_user(db, role)
    assert client.get(f"{API}/admin/health", headers=auth_headers(client, user)).status_code == 403
    assert client.get(f"{API}/admin/alerts", headers=auth_headers(client, user)).status_code == 403


def test_the_report_has_every_metric_block(client: TestClient, db: Session) -> None:
    for kind, status in (
        (DISCOVERY_JOB_KIND, JobRunStatus.done),
        (DISCOVERY_JOB_KIND, JobRunStatus.failed),
        ("audit", JobRunStatus.done),
    ):
        _finished_run(db, kind, status)

    response = client.get(f"{API}/admin/health", headers=auth_headers(client, _tech_admin(db)))
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) >= METRIC_BLOCKS
    assert body["db"] is True and body["redis"] is True
    by_kind = {row["kind"]: row for row in body["jobs"]["last_24h"]}
    assert by_kind["discovery"] == {
        "kind": "discovery",
        "done": 1,
        "failed": 1,
        "cancelled": 0,
        "total": 2,
        "success_rate": 0.5,
    }
    assert by_kind["audit"]["success_rate"] == 1.0
    assert {row["kind"] for row in body["jobs"]["last_7d"]} == {"discovery", "audit"}
    timing = {row["kind"]: row for row in body["timings"]}
    assert timing["discovery"]["runs"] == 1
    assert 9.0 <= timing["discovery"]["median_seconds"] <= 11.0
    assert 9.0 <= timing["discovery"]["p95_seconds"] <= 11.0
    assert body["queue"]["name"] == "default"
    assert isinstance(body["queue"]["length"], int)
    assert {job["name"] for job in body["queue"]["schedule"]} == {
        "crm-sync",
        "watchdog",
        "purge-expired",
        "backup",
        "backup-verify",
    }
    assert set(body["ai"]) >= {
        "provider",
        "calls_today",
        "reuse_rate",
        "spent_today_usd",
        "budget_usd",
        "budget_ratio",
    }
    assert set(body["data_quality"]) == {
        "records_total",
        "records_invalid",
        "invalid_rate",
        "businesses_total",
        "missing_city",
        "missing_phone",
        "missing_website",
    }
    assert set(body["duplicates"]) == {
        "auto_merged",
        "sent_to_review",
        "merged_by_review",
        "kept_apart",
        "pending_review",
    }
    assert set(body["crm"]) == {"destination", "scheduled", "held", "synced_today"}
    assert set(body["audits"]) == {
        "done",
        "robots_blocked",
        "unreachable",
        "failed",
        "skipped",
        "total",
    }
    assert set(body["backups"]) >= {
        "last_backup_at",
        "last_backup_file",
        "last_verify_at",
        "last_verify_ok",
        "last_verify_error",
    }
    assert body["thresholds"]["job_success_rate_min"] == 0.8
    assert body["thresholds"]["watchdog_stale_minutes"] == 30


def test_freshness_and_error_rate_come_from_sources_and_api_calls(
    client: TestClient, db: Session
) -> None:
    source = Source(name="google_places", kind=SourceKind.api, config={}, enabled=True)
    db.add(source)
    db.flush()
    for status, error in ((200, None), (200, None), (500, None), (None, "ConnectError")):
        db.add(
            ApiCall(
                source_id=source.id,
                endpoint="https://places.googleapis.com/v1/places:searchText",
                status_code=status,
                error_class=error,
                duration_ms=5,
            )
        )
    db.commit()

    body = client.get(f"{API}/admin/health", headers=auth_headers(client, _tech_admin(db))).json()

    assert body["sources"] == [
        {"source": "google_places", "calls": 4, "errors": 2, "error_rate": 0.5}
    ]
    fresh = {row["source"]: row for row in body["freshness"]}
    assert fresh["google_places"]["records"] == 0
    assert fresh["google_places"]["last_discovered_at"] is None
    # ...and 50% > 20% fired the source alert.
    assert [a["rule"] for a in body["alerts"]] == [alerts.RULE_SOURCE_ERROR_RATE]


def test_data_quality_counts_missing_fields(client: TestClient, db: Session) -> None:
    db.add(Business(display_name="No phone", city="Austin", website="https://a.test/"))
    db.add(Business(display_name="No city", phone_e164="+15125550100"))
    db.commit()

    body = client.get(f"{API}/admin/health", headers=auth_headers(client, _tech_admin(db))).json()

    assert body["data_quality"]["businesses_total"] == 2
    assert body["data_quality"]["missing_city"] == 1
    assert body["data_quality"]["missing_phone"] == 1
    assert body["data_quality"]["missing_website"] == 1


# --- one test per alert rule -----------------------------------------------------------------


def _open_rules(db: Session) -> list[str]:
    db.expire_all()
    return sorted(
        db.scalars(
            select(Alert.rule).where(Alert.cleared_at.is_(None), Alert.acknowledged_at.is_(None))
        )
    )


def test_job_success_rate_below_80_percent_fires_and_clears(db: Session, fake_redis: Any) -> None:
    for _ in range(3):
        _finished_run(db, DISCOVERY_JOB_KIND, JobRunStatus.failed)
    _finished_run(db, DISCOVERY_JOB_KIND, JobRunStatus.done)

    monitoring.evaluate_alerts(db)
    assert _open_rules(db) == [alerts.RULE_JOB_SUCCESS_RATE]
    alert = db.scalars(select(Alert).where(Alert.rule == alerts.RULE_JOB_SUCCESS_RATE)).one()
    assert "discovery (25%)" in alert.message

    for _ in range(12):
        _finished_run(db, DISCOVERY_JOB_KIND, JobRunStatus.done)
    monitoring.evaluate_alerts(db)
    assert _open_rules(db) == []
    db.expire_all()
    assert alert.cleared_at is not None, "the condition cleared, so the alert did"


def test_a_source_error_rate_above_20_percent_fires(db: Session, fake_redis: Any) -> None:
    source = Source(name="pagespeed_insights", kind=SourceKind.api, config={}, enabled=True)
    db.add(source)
    db.flush()
    for status in (200, 200, 200, 429):
        db.add(ApiCall(source_id=source.id, endpoint="psi", status_code=status, duration_ms=1))
    db.commit()

    monitoring.evaluate_alerts(db)

    assert _open_rules(db) == [alerts.RULE_SOURCE_ERROR_RATE]


def test_a_held_crm_lead_fires(db: Session, fake_redis: Any) -> None:
    business = Business(display_name="Held Co")
    db.add(business)
    db.flush()
    db.add(
        CrmLead(
            business_id=business.id,
            destination=get_settings().crm_destination,
            status=CrmLeadStatus.held,
            last_error="CrmAuthError",
        )
    )
    db.commit()

    monitoring.evaluate_alerts(db)

    assert _open_rules(db) == [alerts.RULE_CRM_HELD]


def test_ai_spend_over_80_percent_of_the_budget_fires(
    db: Session, fake_redis: fakeredis.FakeStrictRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_DAILY_CALL_CAP", "10")
    get_settings.cache_clear()
    budget = AIBudget(fake_redis)
    for _ in range(9):
        budget.record(None)  # no prices: the ratio is calls / cap

    monitoring.evaluate_alerts(db)

    assert _open_rules(db) == [alerts.RULE_AI_BUDGET]


def test_no_backup_in_36_hours_fires_only_once_the_stack_is_old_enough(
    db: Session, fake_redis: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    get_settings.cache_clear()
    user = make_user(db, Role.admin)

    monitoring.evaluate_alerts(db)
    assert _open_rules(db) == [], "a stack minutes old is not expected to have a backup yet"

    user.created_at = datetime.now(UTC) - timedelta(hours=48)
    db.commit()
    monitoring.evaluate_alerts(db)
    assert _open_rules(db) == [alerts.RULE_BACKUP_AGE]

    # A fresh dump clears it; an old one does not.
    (tmp_path / f"radar-{datetime.now(UTC):%Y%m%d-%H%M%S}.dump").write_bytes(b"x")
    monitoring.evaluate_alerts(db)
    assert _open_rules(db) == []


def test_a_backup_older_than_36_hours_fires(
    db: Session, fake_redis: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    get_settings.cache_clear()
    old = datetime.now(UTC) - timedelta(hours=40)
    (tmp_path / f"radar-{old:%Y%m%d-%H%M%S}.dump").write_bytes(b"x")

    monitoring.evaluate_alerts(db)

    assert _open_rules(db) == [alerts.RULE_BACKUP_AGE]


def test_a_queue_longer_than_the_limit_fires(
    db: Session, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALERT_QUEUE_LENGTH_MAX", "2")
    get_settings.cache_clear()
    from app.modules.jobs.service import DEMO_JOB_KIND, enqueue_run

    for _ in range(3):
        enqueue_run(db, search_job_id=None, kind=DEMO_JOB_KIND)

    monitoring.evaluate_alerts(db)

    assert _open_rules(db) == [alerts.RULE_QUEUE_LENGTH]
    alert = db.scalars(select(Alert).where(Alert.rule == alerts.RULE_QUEUE_LENGTH)).one()
    assert alert.severity == "critical"


def test_a_failed_backup_verify_fires_from_the_operator_command(
    db: Session, fake_redis: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "nothing-here"))
    get_settings.cache_clear()
    from app.cli import main

    assert main(["backup-verify"]) == 1

    assert _open_rules(db) == [alerts.RULE_BACKUP_VERIFY_FAILED]


# --- acknowledge -----------------------------------------------------------------------------


def test_an_alert_can_be_acknowledged_and_leaves_the_banner(
    client: TestClient, db: Session, fake_redis: Any
) -> None:
    alerts.raise_alert(db, alerts.RULE_STALE_JOB, "stuck", details={"job_run_id": "x"})
    alerts.raise_alert(db, alerts.RULE_CRM_HELD, "held")
    db.commit()
    admin = make_user(db, Role.admin)
    headers = auth_headers(client, admin)

    listed = client.get(f"{API}/admin/alerts", headers=headers).json()
    assert {a["rule"] for a in listed} == {alerts.RULE_STALE_JOB, alerts.RULE_CRM_HELD}
    stale = next(a for a in listed if a["rule"] == alerts.RULE_STALE_JOB)
    held = next(a for a in listed if a["rule"] == alerts.RULE_CRM_HELD)

    acked = client.post(f"{API}/admin/alerts/{stale['id']}/acknowledge", headers=headers)
    assert acked.status_code == 200, acked.text
    assert acked.json()["acknowledged"] is True
    assert acked.json()["active"] is False, "an event alert closes on acknowledge"

    acked_held = client.post(f"{API}/admin/alerts/{held['id']}/acknowledge", headers=headers).json()
    assert acked_held["acknowledged"] is True
    assert acked_held["active"] is True, "a condition alert stays open until it clears"

    banner = client.get(f"{API}/admin/alerts", headers=headers).json()
    assert banner == []
    everything = client.get(
        f"{API}/admin/alerts", params={"include_acknowledged": "true"}, headers=headers
    ).json()
    assert {a["rule"] for a in everything} == {alerts.RULE_CRM_HELD}
    assert (
        client.post(f"{API}/admin/alerts/{uuid.uuid4()}/acknowledge", headers=headers).status_code
        == 404
    )

    db.expire_all()
    from app.modules.audit.models import AuditLog

    actions = list(db.scalars(select(AuditLog.action)))
    assert actions.count("alert.acknowledged") == 2
    assert "alert.raised" in actions


def test_an_acknowledged_condition_does_not_refire_until_it_clears(
    db: Session, fake_redis: Any
) -> None:
    business = Business(display_name="Held Co")
    db.add(business)
    db.flush()
    lead = CrmLead(
        business_id=business.id,
        destination=get_settings().crm_destination,
        status=CrmLeadStatus.held,
    )
    db.add(lead)
    db.commit()
    admin = make_user(db, Role.admin)

    monitoring.evaluate_alerts(db)
    alert = db.scalars(select(Alert).where(Alert.rule == alerts.RULE_CRM_HELD)).one()
    alerts.acknowledge(db, alert.id, actor_id=admin.id)
    db.commit()

    monitoring.evaluate_alerts(db)
    assert _open_rules(db) == [], "still held, still hidden"
    assert db.scalar(select(Alert).where(Alert.rule == alerts.RULE_CRM_HELD)) is alert

    lead.status = CrmLeadStatus.synced
    db.commit()
    monitoring.evaluate_alerts(db)
    db.expire_all()
    assert alert.cleared_at is not None

    lead.status = CrmLeadStatus.held
    db.commit()
    monitoring.evaluate_alerts(db)
    rows = list(db.scalars(select(Alert).where(Alert.rule == alerts.RULE_CRM_HELD)))
    assert len(rows) == 2, "gone away and come back: a new alert"
    assert _open_rules(db) == [alerts.RULE_CRM_HELD]


def test_backup_status_reads_the_operator_runs(
    client: TestClient,
    db: Session,
    fake_redis: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    get_settings.cache_clear()
    (tmp_path / "radar-20260920-020000.dump").write_bytes(b"dump")
    run_inline(
        db,
        kind="backup-verify",
        work=lambda session, run: {"ok": True, "file": "radar-20260920-020000.dump"},
    )

    body = client.get(f"{API}/admin/health", headers=auth_headers(client, _tech_admin(db))).json()

    assert body["backups"]["last_backup_file"] == "radar-20260920-020000.dump"
    assert body["backups"]["backups_kept"] == 1
    assert body["backups"]["last_verify_ok"] is True
    assert body["backups"]["last_verify_file"] == "radar-20260920-020000.dump"
