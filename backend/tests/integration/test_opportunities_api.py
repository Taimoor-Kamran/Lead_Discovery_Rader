"""The opportunity endpoints, their filters, and who may do what."""

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.opportunities import service
from tests.conftest import auth_headers, make_user
from tests.integration.test_classification_run import (
    BOOKING_MESSAGE,
    answer,
    make_audit,
    make_business,
    make_tools,
)


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer)


@pytest.fixture
def tech_admin(db: Session) -> User:
    return make_user(db, Role.tech_admin)


@pytest.fixture
def classified(db: Session) -> list[Business]:
    """Two businesses: one the AI answered (with an invented email), one rules only."""
    first = make_business(db, name="Alpha Plumbing")
    make_audit(db, first)
    second = make_business(db, name="Beta Roofing")
    second.industry = "roofing"
    second.city = "Round Rock"
    make_audit(db, second, page_text=None)
    scripted = json.loads(answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")))
    scripted["opportunities"][0]["rationale"] = "Audit found no booking; email owner@alpha.invalid"
    tools = make_tools(json.dumps(scripted))
    service.classify(db, first, tools=tools)
    service.classify(db, second, tools=tools)
    db.commit()
    return [first, second]


def test_sales_rep_can_read_opportunities(
    client: TestClient, sales_user: User, classified: list[Business]
) -> None:
    response = client.get("/api/v1/opportunities", headers=auth_headers(client, sales_user))

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["score"] >= items[1]["score"], "best score first"
    for item in items:
        assert set(item["score_components"]) == {"facts", "inference", "intent", "contactability"}
        assert item["scoring_version"] == "scoring-1"
        assert item["review_status"] == "pending"
        assert item["top_evidence"]["text"] and item["top_evidence"]["url"]
        assert item["business_name"]


def test_an_invented_email_never_reaches_the_api(
    client: TestClient, admin_user: User, classified: list[Business]
) -> None:
    headers = auth_headers(client, admin_user)
    listed = client.get("/api/v1/opportunities", headers=headers).json()["items"]
    [item] = [i for i in listed if i["business_name"] == "Alpha Plumbing"]
    detail = client.get(f"/api/v1/opportunities/{item['id']}", headers=headers).json()

    assert "@" not in json.dumps(listed)
    assert "@" not in json.dumps(detail)
    assert detail["source"] == "rules+ai"
    assert detail["ai"]["model"] == "scripted-triage"
    assert detail["ai"]["prompt_version"] == "classify-1"
    assert detail["ai"]["status"] == "guardrail_trimmed"
    assert detail["ai"]["escalated"] is False
    assert detail["ai_agrees"] is True
    assert detail["reason"].startswith("Audit found")
    assert [e["source"] for e in detail["evidence"]] == ["rules", "ai"]
    assert detail["website_audit_id"] and detail["ai_classification_id"]


def test_filters(client: TestClient, admin_user: User, classified: list[Business]) -> None:
    headers = auth_headers(client, admin_user)

    def names(**params: Any) -> list[str]:
        response = client.get("/api/v1/opportunities", params=params, headers=headers)
        assert response.status_code == 200, response.text
        return sorted(i["business_name"] for i in response.json()["items"])

    assert names(service="booking_setup") == ["Alpha Plumbing", "Beta Roofing"]
    assert names(service="seo_gbp") == []
    assert names(industry="roofing") == ["Beta Roofing"]
    assert names(city="round rock") == ["Beta Roofing"]
    assert names(state="tx") == ["Alpha Plumbing", "Beta Roofing"]
    assert names(source="rules+ai") == ["Alpha Plumbing"]
    assert names(source="rules") == ["Beta Roofing"]
    assert names(min_score=0.99) == []
    assert names(review_status="approved") == []
    assert names(sort="created_at") == ["Alpha Plumbing", "Beta Roofing"]


def test_cursor_pagination_by_score(
    client: TestClient, admin_user: User, classified: list[Business]
) -> None:
    headers = auth_headers(client, admin_user)
    first = client.get("/api/v1/opportunities", params={"limit": 1}, headers=headers).json()
    assert first["next_cursor"]
    second = client.get(
        "/api/v1/opportunities",
        params={"limit": 1, "cursor": first["next_cursor"]},
        headers=headers,
    ).json()

    assert second["items"][0]["id"] != first["items"][0]["id"]
    assert second["next_cursor"] is None
    bad = client.get("/api/v1/opportunities", params={"cursor": "nope"}, headers=headers)
    assert bad.status_code == 422


def test_business_opportunities(
    client: TestClient, sales_user: User, classified: list[Business]
) -> None:
    headers = auth_headers(client, sales_user)
    response = client.get(f"/api/v1/businesses/{classified[0].id}/opportunities", headers=headers)

    assert response.status_code == 200
    assert [i["service"] for i in response.json()["items"]] == ["booking_setup"]
    missing = client.get(f"/api/v1/businesses/{uuid.uuid4()}/opportunities", headers=headers)
    assert missing.status_code == 404


def test_unknown_opportunity_is_404(client: TestClient, admin_user: User) -> None:
    response = client.get(
        f"/api/v1/opportunities/{uuid.uuid4()}", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_sales_rep_gets_403_on_classify_and_ai_usage(
    client: TestClient, sales_user: User, classified: list[Business]
) -> None:
    headers = auth_headers(client, sales_user)

    classify = client.post(f"/api/v1/businesses/{classified[0].id}/classify", headers=headers)
    usage = client.get("/api/v1/ai/usage", headers=headers)
    raw = client.get(f"/api/v1/ai/classifications/{uuid.uuid4()}", headers=headers)

    assert classify.status_code == 403
    assert usage.status_code == 403
    assert raw.status_code == 403


def test_reviewer_may_reclassify_a_business_idempotently(
    client: TestClient, reviewer: User, classified: list[Business]
) -> None:
    headers = {**auth_headers(client, reviewer), "Idempotency-Key": "reclass-1"}

    first = client.post(f"/api/v1/businesses/{classified[0].id}/classify", headers=headers)
    second = client.post(f"/api/v1/businesses/{classified[0].id}/classify", headers=headers)

    assert first.status_code == 202, first.text
    assert first.json()["kind"] == "classification"
    assert second.json()["id"] == first.json()["id"]
    assert (
        client.post(f"/api/v1/businesses/{uuid.uuid4()}/classify", headers=headers).status_code
        == 404
    )


def test_only_tech_admin_and_admin_may_classify_a_run(
    client: TestClient, reviewer: User, tech_admin: User, db: Session, classified: list[Business]
) -> None:
    from tests.integration.test_classification_run import audit_run

    run = audit_run(db, classified)

    denied = client.post(f"/api/v1/jobs/{run.id}/classify", headers=auth_headers(client, reviewer))
    allowed = client.post(
        f"/api/v1/jobs/{run.id}/classify", headers=auth_headers(client, tech_admin)
    )

    assert denied.status_code == 403
    assert allowed.status_code == 202, allowed.text
    assert allowed.json()["kind"] == "classification"


def test_ai_usage_and_raw_classification_for_operators(
    client: TestClient, tech_admin: User, db: Session, classified: list[Business]
) -> None:
    headers = auth_headers(client, tech_admin)

    usage = client.get("/api/v1/ai/usage", headers=headers)

    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["calls"] == 1
    assert body["escalations"] == 0
    assert body["tokens_in"] == 100 and body["tokens_out"] == 20
    assert body["est_cost_usd"] == 0.0
    assert body["budget_usd"] == 2.0
    assert body["date"]

    listed = client.get("/api/v1/opportunities", headers=headers).json()["items"]
    [item] = [i for i in listed if i["business_name"] == "Alpha Plumbing"]
    detail = client.get(f"/api/v1/opportunities/{item['id']}", headers=headers).json()
    raw = client.get(
        f"/api/v1/ai/classifications/{detail['ai_classification_id']}", headers=headers
    )

    assert raw.status_code == 200
    assert "owner@alpha.invalid" in raw.json()["raw_output"], "operators see what the model said"
    assert raw.json()["rejected_claims"][0]["rule"] == "pii_in_rationale"
    assert "@" not in json.dumps(raw.json()["output"])

    other_day = client.get("/api/v1/ai/usage", params={"date": "2020-01-01"}, headers=headers)
    assert other_day.json()["calls"] == 0
