"""The Airtable adapter against recorded responses. No test ever calls Airtable.

The token is a sentinel literal so a leak into a log line, an error, an `api_calls` row
or an attempt row is an exact-string match rather than a judgement call.
"""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli
from app.core.config import Settings, get_settings
from app.modules.adapters import registry
from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.crm import adapter as adapters
from app.modules.crm import service
from app.modules.crm.adapter import CrmAuthError, CrmConfigError, CrmRecord, CrmRejectedError
from app.modules.crm.airtable_adapter import (
    API_BASE,
    RECORDS_PER_REQUEST,
    AirtableAdapter,
    build_airtable_adapter,
    load_field_map,
)
from app.modules.crm.fields import CRM_OWNED, LABEL_BY_KEY
from app.modules.crm.models import CrmLeadStatus, CrmSyncAction, CrmSyncAttempt, CrmSyncStatus
from app.modules.discovery.models import ApiCall
from app.modules.opportunities.models import ReviewStatus
from tests.conftest import FakeClock, load_fixture, make_user
from tests.integration.test_classification_run import make_audit, make_business
from tests.integration.test_crm_sync import T0, approve, attempts_of, lead_for, run_due
from tests.integration.test_no_secrets_in_logs import CapturingHandler, captured_logs  # noqa: F401
from tests.integration.test_review_api import make_opportunity

TOKEN = "patAIRTABLE-SENTINEL-4f9c1e-never-log-me"
BASE = "appBASE000000001"
TABLE_URL = f"{API_BASE}/{BASE}/Leads"
META_URL = f"{API_BASE}/meta/bases/{BASE}/tables"
LABEL = LABEL_BY_KEY


def fixture(name: str) -> Any:
    return load_fixture("airtable", name)


def settings_for(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "ci",
        "crm_destination": "airtable",
        "airtable_token": SecretStr(TOKEN),
        "airtable_base_id": BASE,
        "airtable_table": "Leads",
        "airtable_rps": 100,
    }
    values.update(overrides)
    if isinstance(values["airtable_token"], str):
        values["airtable_token"] = SecretStr(values["airtable_token"])
    return Settings(**values)


@pytest.fixture
def airtable(
    db: Session, fake_redis: Any, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Settings]:
    """Airtable as the configured destination, with the real adapter wired to a fake clock."""
    monkeypatch.setenv("CRM_DESTINATION", "airtable")
    monkeypatch.setenv("AIRTABLE_TOKEN", TOKEN)
    monkeypatch.setenv("AIRTABLE_BASE_ID", BASE)
    monkeypatch.setenv("AIRTABLE_TABLE", "Leads")
    monkeypatch.setenv("AIRTABLE_RPS", "100")
    get_settings.cache_clear()
    registry.sync_sources(db)
    db.commit()
    saved = adapters.snapshot()
    adapters.register(
        "airtable",
        lambda session, settings: build_airtable_adapter(
            session, settings, redis_client=fake_redis, sleeper=clock.sleep, clock=clock
        ),
        replace=True,
    )
    yield get_settings()
    adapters.restore(saved)
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer, "reviewer@example.com")


@pytest.fixture
def business(db: Session) -> Business:
    row = make_business(db, name="Barton Creek Plumbing")
    row.domain = "bartoncreekplumbing.invalid"
    row.phone_e164 = "+15125550102"
    make_audit(db, row)
    db.commit()
    return row


def adapter_for(
    db: Session, fake_redis: Any, clock: FakeClock, **overrides: Any
) -> AirtableAdapter:
    return build_airtable_adapter(
        db, settings_for(**overrides), redis_client=fake_redis, sleeper=clock.sleep, clock=clock
    )


def sent_bodies(route: respx.Route) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in route.calls]


# --- check ----------------------------------------------------------------------------------------


def test_check_reports_every_field_ok_against_a_complete_table(
    db: Session, fake_redis: Any, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    mock_http.get(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("meta_tables_ok.json"))
    )
    health = adapter_for(db, fake_redis, clock).check()
    assert health.ok is True
    assert [c.name for c in health.checks[:2]] == ["config", "table"]
    assert all(c.ok for c in health.checks)
    assert {c.name for c in health.checks} >= set(LABEL.values())


def test_check_names_missing_and_wrongly_typed_fields_clearly(
    db: Session,
    fake_redis: Any,
    clock: FakeClock,
    mock_http: respx.MockRouter,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_http.get(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("meta_tables_missing_fields.json"))
    )
    health = adapter_for(db, fake_redis, clock).check()
    assert health.ok is False
    failed = {c.name: c.detail for c in health.checks if not c.ok}
    assert failed == {
        "Legal name": "missing (expected singleLineText)",
        "Lead score": "type 'singleLineText' cannot hold number values",
        "Do not contact": "missing (expected checkbox)",
    }

    # `make crm-check` prints the same table.
    monkeypatch.setenv("CRM_DESTINATION", "airtable")
    monkeypatch.setenv("AIRTABLE_TOKEN", TOKEN)
    monkeypatch.setenv("AIRTABLE_BASE_ID", BASE)
    get_settings.cache_clear()
    try:
        assert cli.main(["crm-check"]) == 1
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "Destination: airtable" in out
    assert "MISSING Legal name" in out and "MISSING Do not contact" in out
    assert "FAIL  Lead score" in out
    assert "OK    Business name" in out
    assert TOKEN not in out


def test_check_without_a_token_or_with_a_bad_one_says_what_to_fix(
    db: Session, fake_redis: Any, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    health = adapter_for(db, fake_redis, clock, airtable_token="").check()
    assert health.ok is False and "AIRTABLE_TOKEN is not set" in (health.message or "")
    assert not mock_http.calls

    mock_http.get(META_URL).mock(return_value=httpx.Response(401, json=fixture("error_401.json")))
    health = adapter_for(db, fake_redis, clock).check()
    assert health.ok is False
    assert "AIRTABLE_TOKEN" in (health.message or "")
    assert TOKEN not in json.dumps([c.detail for c in health.checks])


def test_the_field_map_must_name_every_field(tmp_path: Any) -> None:
    assert load_field_map()["radar_business_id"] == "Radar Business ID"
    partial = tmp_path / "map.json"
    partial.write_text(json.dumps({"radar_business_id": "ID"}), encoding="utf-8")
    with pytest.raises(CrmConfigError) as caught:
        load_field_map(partial)
    assert "business_name" in caught.value.details["missing"]
    assert RECORDS_PER_REQUEST == 10


# --- create, update, find -------------------------------------------------------------------------


def test_create_sends_every_field_and_update_sends_only_radar_owned_ones(
    db: Session, fake_redis: Any, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    mock_http.get(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("meta_tables_ok.json"))
    )
    create = mock_http.post(TABLE_URL).mock(
        return_value=httpx.Response(200, json=fixture("create_ok.json"))
    )
    update = mock_http.patch(f"{TABLE_URL}/recNEW00000000001").mock(
        return_value=httpx.Response(200, json=fixture("update_ok.json"))
    )
    adapter = adapter_for(db, fake_redis, clock)
    record = CrmRecord(
        business_id=__import__("uuid").uuid4(),
        fields={
            "radar_business_id": "b-1",
            "business_name": "Barton Creek Plumbing",
            "legal_name": None,
            "lead_score": 72,
            "do_not_contact": False,
            "assigned_rep": "rep1@example.com",
            "status": "New",
            "follow_up_date": None,
            "notes": "Owner asked for a quote",
        },
    )

    created = adapter.upsert(record, None)
    assert created.action == "created" and created.external_id == "recNEW00000000001"
    assert (
        created.external_url == f"https://airtable.com/{BASE}/tblLeads0000000001/recNEW00000000001"
    )
    [body] = sent_bodies(create)
    assert body["typecast"] is True
    fields = body["records"][0]["fields"]
    assert fields["Status"] == "New" and fields["Assigned rep"] == "rep1@example.com"
    assert fields["Notes"] == "Owner asked for a quote"
    assert "Legal name" not in fields, "nulls are left out on create"
    assert create.calls[0].request.headers["Authorization"] == f"Bearer {TOKEN}"

    updated = adapter.upsert(record, "recNEW00000000001")
    assert updated.action == "updated"
    [body] = sent_bodies(update)
    assert body["fields"]["Business name"] == "Barton Creek Plumbing"
    assert body["fields"]["Legal name"] is None, "a value that became unknown is cleared"
    assert not {LABEL[key] for key in CRM_OWNED} & set(body["fields"])


def test_find_by_keys_asks_by_business_id_then_domain_then_phone(
    db: Session, fake_redis: Any, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    route = mock_http.get(url__startswith=TABLE_URL).mock(
        side_effect=[
            httpx.Response(200, json=fixture("list_empty.json")),
            httpx.Response(200, json=fixture("list_empty.json")),
            httpx.Response(200, json=fixture("list_found.json")),
        ]
    )
    adapter = adapter_for(db, fake_redis, clock)
    found = adapter.find_by_keys("b-1", "BartonCreekPlumbing.invalid", "(512) 555-0102")
    assert found == "recEXIST000000001"
    formulas = [dict(call.request.url.params)["filterByFormula"] for call in route.calls]
    assert formulas == [
        "{Radar Business ID}='b-1'",
        "FIND('bartoncreekplumbing.invalid', LOWER({Website}))",
        "{Public phone}='(512) 555-0102'",
    ]
    assert all(dict(call.request.url.params)["maxRecords"] == "1" for call in route.calls)


def test_do_not_contact_and_withdraw_touch_one_field_each_and_respect_a_changed_status(
    db: Session, fake_redis: Any, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    mock_http.get(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("meta_tables_ok.json"))
    )
    patch = mock_http.patch(f"{TABLE_URL}/recNEW00000000001").mock(
        return_value=httpx.Response(200, json=fixture("record_new.json"))
    )
    get = mock_http.get(f"{TABLE_URL}/recNEW00000000001").mock(
        side_effect=[
            httpx.Response(200, json=fixture("record_new.json")),
            httpx.Response(200, json=fixture("update_ok.json")),
        ]
    )
    adapter = adapter_for(db, fake_redis, clock)

    adapter.mark_do_not_contact("recNEW00000000001", "owner asked", flag=True)
    assert sent_bodies(patch)[-1]["fields"] == {"Do not contact": True}
    adapter.mark_do_not_contact("recNEW00000000001", "lifted", flag=False)
    assert sent_bodies(patch)[-1]["fields"] == {"Do not contact": False}

    assert adapter.withdraw("recNEW00000000001").action == "updated"
    assert sent_bodies(patch)[-1]["fields"] == {"Status": "Withdrawn"}
    assert adapter.withdraw("recNEW00000000001").action == "unchanged", "Status is Contacted now"
    assert len(patch.calls) == 3 and len(get.calls) == 2


def test_bootstrap_creates_the_table_once(
    db: Session, fake_redis: Any, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    mock_http.get(META_URL).mock(
        side_effect=[
            httpx.Response(200, json={"tables": []}),
            httpx.Response(200, json=fixture("meta_tables_ok.json")),
        ]
    )
    create = mock_http.post(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("create_table_ok.json"))
    )
    adapter = adapter_for(db, fake_redis, clock)
    assert adapter.bootstrap_table() == "tblLeads0000000001"
    [body] = sent_bodies(create)
    assert body["name"] == "Leads"
    names = [field["name"] for field in body["fields"]]
    assert names[0] == "Radar Business ID" and set(names) == set(LABEL.values())
    by_name = {field["name"]: field for field in body["fields"]}
    assert by_name["Lead score"] == {
        "name": "Lead score",
        "type": "number",
        "options": {"precision": 0},
    }
    assert by_name["Do not contact"]["type"] == "checkbox"
    assert by_name["Date approved"]["options"] == {"dateFormat": {"name": "iso"}}
    with pytest.raises(CrmConfigError, match="already exists"):
        adapter.bootstrap_table()


# --- errors → lead states -------------------------------------------------------------------------


def test_error_classes(
    db: Session, fake_redis: Any, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    adapter = adapter_for(db, fake_redis, clock)
    record = CrmRecord(business_id=__import__("uuid").uuid4(), fields={"radar_business_id": "b"})
    mock_http.post(TABLE_URL).mock(return_value=httpx.Response(403, json=fixture("error_401.json")))
    with pytest.raises(CrmAuthError):
        adapter.upsert(record, None)
    mock_http.post(TABLE_URL).mock(
        return_value=httpx.Response(422, json=fixture("error_422_unknown_field.json"))
    )
    with pytest.raises(CrmConfigError, match="crm-check"):
        adapter.upsert(record, None)
    mock_http.post(TABLE_URL).mock(
        return_value=httpx.Response(422, json=fixture("error_422_invalid_value.json"))
    )
    with pytest.raises(CrmRejectedError):
        adapter.upsert(record, None)
    mock_http.post(TABLE_URL).mock(return_value=httpx.Response(404, json=fixture("error_404.json")))
    with pytest.raises(CrmConfigError, match="404"):
        adapter.upsert(record, None)


def test_a_full_sync_against_airtable_retries_429_and_5xx_then_holds_auth_errors(
    db: Session,
    business: Business,
    reviewer: User,
    airtable: Settings,
    mock_http: respx.MockRouter,
    captured_logs: CapturingHandler,  # noqa: F811
) -> None:
    mock_http.get(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("meta_tables_ok.json"))
    )
    mock_http.get(url__startswith=TABLE_URL).mock(
        return_value=httpx.Response(200, json=fixture("list_empty.json"))
    )
    create = mock_http.post(TABLE_URL).mock(
        side_effect=[
            httpx.Response(429, json=fixture("error_429.json"), headers={"Retry-After": "30"}),
            httpx.Response(502, json={"error": "bad gateway"}),
            httpx.Response(200, json=fixture("create_ok.json")),
        ]
    )
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    due = T0 + timedelta(minutes=30)

    run_due(db, now=due)
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled and lead.attempts == 1
    assert lead.due_at == due + timedelta(seconds=30), "Retry-After from Airtable is honoured"

    run_due(db, now=due + timedelta(seconds=30))
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.scheduled and lead.attempts == 2
    assert lead.due_at == due + timedelta(seconds=30 + 120)

    run_due(db, now=due + timedelta(seconds=200))
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.synced
    assert lead.external_id == "recNEW00000000001"
    assert lead.external_url == f"https://airtable.com/{BASE}/tblLeads0000000001/recNEW00000000001"
    assert len(create.calls) == 3
    rows = attempts_of(db, lead)
    assert [(a.action, a.status, a.http_status) for a in rows] == [
        (CrmSyncAction.create, CrmSyncStatus.failed, 429),
        (CrmSyncAction.create, CrmSyncStatus.failed, 502),
        (CrmSyncAction.create, CrmSyncStatus.ok, None),
    ]
    created_fields = sent_bodies(create)[-1]["records"][0]["fields"]
    assert created_fields["Radar Business ID"] == str(business.id)
    assert created_fields["Services"] == "Website redesign"
    assert created_fields["Public phone"] == "(512) 555-0102"

    # An auth failure on a later update holds the lead at once, with the variable name.
    other = make_opportunity(db, business, "seo_gbp")
    db.commit()
    approve(db, reviewer, other, now=due + timedelta(hours=1))
    mock_http.patch(f"{TABLE_URL}/recNEW00000000001").mock(
        return_value=httpx.Response(401, json=fixture("error_401.json"))
    )
    run_due(db, now=due + timedelta(hours=2))
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.held and lead.attempts == 1
    assert lead.last_error is not None and "AIRTABLE_TOKEN" in lead.last_error

    # The token is nowhere: not in logs, errors, api_calls or attempts.
    everything = " ".join(
        [
            "\n".join(captured_logs.lines),
            lead.last_error or "",
            *[str(a.error) for a in db.scalars(select(CrmSyncAttempt))],
            *[f"{c.endpoint} {c.error_class}" for c in db.scalars(select(ApiCall))],
        ]
    )
    assert TOKEN not in everything
    assert len(list(db.scalars(select(ApiCall)))) >= 4, "every Airtable call is metered"


def test_an_unknown_field_holds_immediately_and_retry_from_held_works(
    db: Session, business: Business, reviewer: User, airtable: Settings, mock_http: respx.MockRouter
) -> None:
    mock_http.get(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("meta_tables_ok.json"))
    )
    mock_http.get(url__startswith=TABLE_URL).mock(
        return_value=httpx.Response(200, json=fixture("list_empty.json"))
    )
    create = mock_http.post(TABLE_URL).mock(
        side_effect=[
            httpx.Response(422, json=fixture("error_422_unknown_field.json")),
            httpx.Response(200, json=fixture("create_ok.json")),
        ]
    )
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    run_due(db, now=T0 + timedelta(hours=1))
    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.held and lead.attempts == 1
    assert lead.last_error is not None and "crm-check" in lead.last_error

    read = service.retry(db, lead.id, actor=reviewer, now=T0 + timedelta(hours=2))
    db.commit()
    assert read.status is CrmLeadStatus.synced and len(create.calls) == 2


def test_an_existing_airtable_record_is_linked_not_duplicated(
    db: Session, business: Business, reviewer: User, airtable: Settings, mock_http: respx.MockRouter
) -> None:
    mock_http.get(META_URL).mock(
        return_value=httpx.Response(200, json=fixture("meta_tables_ok.json"))
    )
    listing = mock_http.get(url__startswith=TABLE_URL).mock(
        side_effect=[
            httpx.Response(200, json=fixture("list_empty.json")),  # by Radar Business ID: no
            httpx.Response(200, json=fixture("list_found.json")),  # by domain: yes
        ]
    )
    create = mock_http.post(TABLE_URL)
    update = mock_http.patch(f"{TABLE_URL}/recEXIST000000001").mock(
        return_value=httpx.Response(200, json=fixture("update_ok.json"))
    )
    row = make_opportunity(db, business)
    db.commit()
    approve(db, reviewer, row, now=T0)
    run_due(db, now=T0 + timedelta(hours=1))

    lead = lead_for(db, business)
    assert lead.status is CrmLeadStatus.synced and lead.external_id == "recEXIST000000001"
    assert len(create.calls) == 0, "linked, not duplicated"
    assert len(listing.calls) == 2 and len(update.calls) == 1
    [body] = sent_bodies(update)
    assert not {LABEL[key] for key in CRM_OWNED} & set(body["fields"])
    assert [a.action for a in attempts_of(db, lead)] == [CrmSyncAction.link, CrmSyncAction.update]


def test_the_gate_holds_for_airtable_too(
    db: Session, business: Business, reviewer: User, airtable: Settings, mock_http: respx.MockRouter
) -> None:
    """Belt and braces on top of the parametrized gate test: no route, so any call would raise."""
    make_opportunity(db, business, status=ReviewStatus.rejected)
    db.commit()
    with pytest.raises(Exception, match=r"not_eligible|no approved"):
        service.sync_now(db, business.id, actor=reviewer, now=T0)
    assert not mock_http.calls
    mock_http.get(META_URL).mock(return_value=httpx.Response(503, json={"error": "down"}))
    assert service.status(db).health.ok is False, "Airtable is down: unreachable, not lying"


def test_the_token_is_scrubbed_from_a_plain_log_line(
    airtable: Settings,
    captured_logs: CapturingHandler,  # noqa: F811
) -> None:
    logging.getLogger("app.test").warning(
        "calling airtable with %s", TOKEN, extra={"airtable_token": TOKEN}
    )
    output = "\n".join(captured_logs.lines)
    assert TOKEN not in output and "[REDACTED]" in output


def test_now_is_utc(business: Business) -> None:
    assert datetime.now(UTC).tzinfo is UTC
