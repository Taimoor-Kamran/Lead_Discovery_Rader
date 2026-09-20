"""`/crm/*` and the `crm` block on `/leads`: RBAC for every endpoint, status, list, retry,
send now, sync all, the CSV export (columns, BOM, quoting, injection, scope, marking)."""

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.crm import adapter as adapters
from app.modules.crm import service
from app.modules.crm.adapter import CrmAuthError
from app.modules.crm.csv_adapter import BOM, COLUMNS
from app.modules.crm.models import CrmLeadStatus, CrmSyncAction, CrmSyncAttempt, FakeCrmRecord
from app.modules.opportunities.models import ReviewStatus
from app.modules.review import service as review
from app.modules.review.models import Decision
from app.modules.review.schemas import ReviewRequest
from tests.conftest import auth_headers, make_user
from tests.integration.test_classification_run import make_audit, make_business
from tests.integration.test_crm_sync import DELAY, Spy, approve, lead_for, run_due
from tests.integration.test_review_api import make_opportunity

API = "/api/v1"
# The endpoints run at the real clock, so approvals here are stamped a day ago: well past
# their undo window whatever the time of day the suite runs.
PAST = datetime.now(UTC) - timedelta(days=1)


@pytest.fixture
def spy() -> Iterator[Spy]:
    saved = adapters.snapshot()
    watcher = Spy()
    watcher.install("fake")
    yield watcher
    adapters.restore(saved)


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer, "reviewer@example.com")


@pytest.fixture
def crm_manager(db: Session) -> User:
    return make_user(db, Role.crm_manager, "crm@example.com")


@pytest.fixture
def tech_admin(db: Session) -> User:
    return make_user(db, Role.tech_admin)


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


def approved_lead(
    db: Session, reviewer: User, business: Business, *, rep: User | None = None
) -> Any:
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=PAST, rep=rep)
    return row


# --- RBAC -----------------------------------------------------------------------------------------


ENDPOINTS: list[tuple[str, str, set[str]]] = [
    ("GET", "/crm/status", {"admin", "crm_manager", "tech_admin"}),
    ("GET", "/crm/leads", {"admin", "crm_manager"}),
    ("GET", "/crm/leads/{lead}/attempts", {"admin", "crm_manager", "tech_admin"}),
    ("POST", "/crm/leads/{lead}/retry", {"admin", "crm_manager"}),
    ("POST", "/crm/businesses/{business}/sync-now", {"admin", "crm_manager"}),
    ("POST", "/crm/sync-all", {"admin", "crm_manager"}),
    ("GET", "/crm/export.csv", {"admin", "crm_manager"}),
    ("GET", "/crm/fake-records/{record}", {"admin", "crm_manager", "tech_admin"}),
]


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("method", "path", "allowed"), ENDPOINTS)
def test_rbac_for_every_crm_endpoint(
    client: TestClient,
    db: Session,
    reviewer: User,
    business: Business,
    spy: Spy,
    role: Role,
    method: str,
    path: str,
    allowed: set[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved_lead(db, reviewer, business)
    lead = lead_for(db, business)
    if "retry" in path:
        lead.status = CrmLeadStatus.held
        db.commit()
    if path.endswith("export.csv"):
        monkeypatch.setenv("CRM_DESTINATION", "csv")
        get_settings.cache_clear()
    record_id = uuid.uuid4()
    if "fake-records" in path:
        db.add(FakeCrmRecord(id=record_id, fields={"Business name": "x"}))
        db.commit()
    user = make_user(db, role)
    url = API + path.format(lead=lead.id, business=business.id, record=record_id)
    try:
        response = client.request(method, url, headers=auth_headers(client, user))
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    if role.value in allowed:
        assert response.status_code == 200, (role, path, response.text)
    else:
        assert response.status_code == 403, (role, path, response.text)
    anonymous = client.request(method, url)
    assert anonymous.status_code == 401


# --- status and lists -----------------------------------------------------------------------------


def test_status_reports_the_fake_destination_health_and_counts(
    client: TestClient, db: Session, reviewer: User, crm_manager: User, business: Business, spy: Spy
) -> None:
    approved_lead(db, reviewer, business)
    body = client.get(f"{API}/crm/status", headers=auth_headers(client, crm_manager)).json()
    assert body["destination"] == "fake" and body["demo"] is True
    assert body["auto_sync"] is True and body["sync_delay_minutes"] == 30
    assert body["health"]["ok"] is True
    assert body["health"]["message"].startswith("Destination: fake (demo)")
    assert body["counts"]["scheduled"] == 1 and body["counts"]["synced"] == 0


def test_the_lead_list_filters_by_status_and_shows_errors(
    client: TestClient, db: Session, reviewer: User, crm_manager: User, business: Business, spy: Spy
) -> None:
    approved_lead(db, reviewer, business)
    spy.failures.append(CrmAuthError("bad token", http_status=401))
    run_due(db, now=PAST + DELAY)
    headers = auth_headers(client, crm_manager)

    held = client.get(f"{API}/crm/leads", params={"status": "held"}, headers=headers).json()
    assert len(held["items"]) == 1
    item = held["items"][0]
    assert item["business_name"] == "Barton Creek Plumbing" and item["city"] == "Austin"
    assert item["attempts"] == 1 and "bad token" in item["last_error"]
    assert item["services"] == []
    assert (
        client.get(f"{API}/crm/leads", params={"status": "synced"}, headers=headers).json()["items"]
        == []
    )

    attempts = client.get(f"{API}/crm/leads/{item['id']}/attempts", headers=headers).json()
    assert [(a["action"], a["status"], a["http_status"]) for a in attempts] == [
        ("create", "failed", 401)
    ]

    retried = client.post(f"{API}/crm/leads/{item['id']}/retry", headers=headers)
    assert retried.status_code == 200 and retried.json()["status"] == "synced"
    assert retried.json()["services"] == ["Website redesign"]
    again = client.post(f"{API}/crm/leads/{item['id']}/retry", headers=headers)
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_held"


def test_sync_now_enforces_the_gate_and_sync_all_reports_counts(
    client: TestClient, db: Session, reviewer: User, crm_manager: User, business: Business, spy: Spy
) -> None:
    headers = auth_headers(client, crm_manager)
    make_opportunity(db, business, status=ReviewStatus.rejected)
    db.commit()
    refused = client.post(f"{API}/crm/businesses/{business.id}/sync-now", headers=headers)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_eligible"
    assert spy.calls == []

    approved_lead(db, reviewer, business)
    sent = client.post(f"{API}/crm/businesses/{business.id}/sync-now", headers=headers)
    assert sent.status_code == 200 and sent.json()["status"] == "synced"
    assert sent.json()["external_id"] is not None

    result = client.post(f"{API}/crm/sync-all", headers=headers).json()
    assert result == {"considered": 0, "synced": 0, "held": 0, "scheduled": 0, "cancelled": 0}
    missing = client.post(f"{API}/crm/businesses/{business.id.hex[:8]}/sync-now", headers=headers)
    assert missing.status_code == 422


def test_the_fake_record_can_be_read_for_the_demo_and_is_hidden_elsewhere(
    client: TestClient,
    db: Session,
    reviewer: User,
    crm_manager: User,
    business: Business,
    spy: Spy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved_lead(db, reviewer, business)
    headers = auth_headers(client, crm_manager)
    sent = client.post(f"{API}/crm/businesses/{business.id}/sync-now", headers=headers).json()
    record = client.get(f"{API}/crm/fake-records/{sent['external_id']}", headers=headers)
    assert record.status_code == 200
    assert record.json()["fields"]["Business name"] == "Barton Creek Plumbing"
    assert record.json()["fields"]["Do not contact"] is False
    missing = client.get(f"{API}/crm/fake-records/{business.id}", headers=headers)
    assert missing.status_code == 404
    monkeypatch.setenv("CRM_DESTINATION", "csv")
    get_settings.cache_clear()
    hidden = client.get(f"{API}/crm/fake-records/{sent['external_id']}", headers=headers)
    monkeypatch.undo()
    get_settings.cache_clear()
    assert hidden.status_code == 404


# --- the crm block on leads -----------------------------------------------------------------------


def test_leads_carry_the_crm_block_and_a_rep_sees_only_their_own(
    client: TestClient, db: Session, reviewer: User, rep: User, business: Business, spy: Spy
) -> None:
    mine = approved_lead(db, reviewer, business, rep=rep)
    other_business = make_business(db, name="Someone Else's")
    other_business.phone_e164 = "+15125550199"
    make_audit(db, other_business)
    other_rep = make_user(db, Role.sales_rep)
    theirs = approved_lead(db, reviewer, other_business, rep=other_rep)
    run_due(db, now=PAST + DELAY)

    page = client.get(f"{API}/leads", headers=auth_headers(client, rep)).json()
    assert [item["opportunity_id"] for item in page["items"]] == [str(mine.id)]
    block = page["items"][0]["crm"]
    assert block["status"] == "synced" and block["last_synced_at"] is not None
    assert set(block) == {"id", "status", "external_url", "last_synced_at", "due_at", "last_error"}

    detail = client.get(f"{API}/leads/{mine.id}", headers=auth_headers(client, rep)).json()
    assert detail["lead"]["crm"]["status"] == "synced"
    assert [a["action"] for a in detail["crm_history"]] == ["create"]
    forbidden = client.get(f"{API}/leads/{theirs.id}", headers=auth_headers(client, rep))
    assert forbidden.status_code == 403

    everything = client.get(f"{API}/leads", headers=auth_headers(client, reviewer)).json()
    assert len(everything["items"]) == 2


def test_a_lead_without_a_scheduled_sync_has_a_null_crm_block(
    client: TestClient, db: Session, reviewer: User, business: Business
) -> None:
    row = make_opportunity(db, business, status=ReviewStatus.approved)
    row.decided_at = PAST
    row.decided_by = reviewer.id
    db.commit()
    page = client.get(f"{API}/leads", headers=auth_headers(client, reviewer)).json()
    assert page["items"][0]["crm"] is None


# --- CSV export -----------------------------------------------------------------------------------


@pytest.fixture
def csv_destination(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CRM_DESTINATION", "csv")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def parse_csv(response: Any) -> tuple[list[str], list[dict[str, str]]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    raw = response.content
    assert raw.startswith(BOM.encode("utf-8")), "UTF-8 BOM for Excel"
    text = raw.decode("utf-8").removeprefix(BOM)
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    return header, [dict(zip(header, row, strict=True)) for row in body]


def test_the_csv_export_has_the_blueprint_columns_neutralises_formulas_and_marks_rows(
    client: TestClient,
    db: Session,
    reviewer: User,
    crm_manager: User,
    rep: User,
    csv_destination: None,
) -> None:
    hostile = make_business(db, name='=HYPERLINK("http://evil.invalid","Plumb, "Bob"")')
    hostile.domain = "evil-plumbing.invalid"
    hostile.phone_e164 = "+15125550102"
    make_audit(db, hostile)
    approved_lead(db, reviewer, hostile, rep=rep)
    plain = make_business(db, name="Zilker Pipeworks")
    plain.phone_e164 = "+15125550188"
    plain.domain = "zilker.invalid"
    make_audit(db, plain)
    approved_lead(db, reviewer, plain)
    run_due(db, now=PAST + DELAY)
    headers = auth_headers(client, crm_manager)

    response = client.get(f"{API}/crm/export.csv", headers=headers)
    header, rows = parse_csv(response)
    assert header == list(COLUMNS)
    assert header[:3] == ["Radar Business ID", "Business name", "Legal name"]
    assert 'filename="radar-leads-' in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.csv"')
    by_name = {row["Radar Business ID"]: row for row in rows}
    evil = by_name[str(hostile.id)]
    assert evil["Business name"] == '\'=HYPERLINK("http://evil.invalid","Plumb, "Bob"")'
    assert evil["Services"] == "Website redesign"
    assert evil["Lead score"] == "70"
    assert evil["Public phone"] == "(512) 555-0102"
    assert evil["Assigned rep"] == "rep1@example.com"
    assert evil["Status"] == "New" and evil["Follow-up date"] == ""
    assert evil["Do not contact"] == "false"
    assert evil["Approved by"] == "reviewer@example.com"
    assert evil["Date approved"] == PAST.date().isoformat()
    assert evil["Radar link"].startswith("http://localhost:3000/leads/")
    assert evil["Industry"] == "Plumbing"
    assert not any(value.startswith(("=", "+", "-", "@")) for row in rows for value in row.values())
    assert len(rows) == 2

    # Exporting marks the rows; `new` is now empty, `all` has both.
    for lead in (lead_for(db, hostile), lead_for(db, plain)):
        assert lead.export_batch_id is not None and lead.status is CrmLeadStatus.synced
    _, again = parse_csv(client.get(f"{API}/crm/export.csv", headers=headers))
    assert again == []
    _, everything = parse_csv(
        client.get(f"{API}/crm/export.csv", params={"scope": "all"}, headers=headers)
    )
    assert len(everything) == 2
    exports = list(
        db.scalars(select(CrmSyncAttempt).where(CrmSyncAttempt.action == CrmSyncAction.export))
    )
    assert len(exports) == 4

    bad = client.get(f"{API}/crm/export.csv", params={"scope": "everything"}, headers=headers)
    assert bad.status_code == 422


def test_do_not_contact_after_an_export_shows_in_the_next_full_export(
    client: TestClient,
    db: Session,
    reviewer: User,
    crm_manager: User,
    business: Business,
    csv_destination: None,
) -> None:
    approved_lead(db, reviewer, business)
    run_due(db, now=PAST + DELAY)
    headers = auth_headers(client, crm_manager)
    _, first = parse_csv(client.get(f"{API}/crm/export.csv", headers=headers))
    assert first[0]["Do not contact"] == "false"

    pending = make_opportunity(db, business, "seo_gbp")
    db.commit()
    review.decide(
        db,
        pending.id,
        ReviewRequest(
            decision=Decision.do_not_contact, lock_version=0, note="Owner asked us to stop"
        ),
        actor=reviewer,
        now=PAST + timedelta(hours=1),
    )
    db.commit()
    run_due(db, now=PAST + timedelta(hours=1))

    _, fresh = parse_csv(client.get(f"{API}/crm/export.csv", headers=headers))
    assert len(fresh) == 1 and fresh[0]["Do not contact"] == "true", "changed since the last export"
    _, everything = parse_csv(
        client.get(f"{API}/crm/export.csv", params={"scope": "all"}, headers=headers)
    )
    assert everything[0]["Do not contact"] == "true"
    assert everything[0]["Business name"] == "Barton Creek Plumbing", "never deleted"


def test_the_export_is_refused_for_another_destination(
    client: TestClient, db: Session, crm_manager: User, spy: Spy
) -> None:
    response = client.get(f"{API}/crm/export.csv", headers=auth_headers(client, crm_manager))
    assert response.status_code == 422
    assert "CRM_DESTINATION=csv" in response.json()["error"]["message"]


def test_csv_status_is_healthy_and_leads_that_never_passed_the_gate_are_not_exported(
    client: TestClient,
    db: Session,
    reviewer: User,
    crm_manager: User,
    business: Business,
    csv_destination: None,
) -> None:
    make_opportunity(db, business, status=ReviewStatus.pending)
    db.commit()
    headers = auth_headers(client, crm_manager)
    status = client.get(f"{API}/crm/status", headers=headers).json()
    assert (
        status["destination"] == "csv"
        and status["demo"] is False
        and status["health"]["ok"] is True
    )
    _, rows = parse_csv(
        client.get(f"{API}/crm/export.csv", params={"scope": "all"}, headers=headers)
    )
    assert rows == []
    assert service.status(db).counts["synced"] == 0
