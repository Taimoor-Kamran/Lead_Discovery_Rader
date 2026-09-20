"""`/suppressions`: adding by business, domain or phone; lifting; who may do what."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from tests.conftest import auth_headers, make_user
from tests.integration.test_classification_run import make_business


def add(client: TestClient, user: User, **body: Any) -> Any:
    return client.post("/api/v1/suppressions", json=body, headers=auth_headers(client, user))


def test_admin_adds_by_domain_phone_or_business_and_lifts(
    client: TestClient, db: Session, admin_user: User
) -> None:
    business: Business = make_business(db, name="Suppress Me Plumbing")
    db.commit()
    headers = auth_headers(client, admin_user)

    by_domain = add(
        client, admin_user, domain="https://www.Example-Site.com/contact", reason="asked"
    )
    assert by_domain.status_code == 201, by_domain.text
    assert by_domain.json()["domain"] == "example-site.com"
    assert by_domain.json()["source"] == "admin"
    assert by_domain.json()["active"] is True

    by_phone = add(client, admin_user, phone_e164="(512) 555-0100", reason="asked")
    assert by_phone.status_code == 201, by_phone.text
    assert by_phone.json()["phone_e164"] == "+15125550100"

    by_business = add(client, admin_user, business_id=str(business.id), reason="client of ours")
    assert by_business.status_code == 201, by_business.text
    body = by_business.json()
    assert body["business_id"] == str(business.id)
    assert body["domain"] == business.domain
    assert body["phone_e164"] == business.phone_e164

    listed = client.get("/api/v1/suppressions", headers=headers).json()["items"]
    assert len(listed) == 3
    assert listed[0]["business_name"] == "Suppress Me Plumbing"

    lifted = client.post(f"/api/v1/suppressions/{body['id']}/lift", headers=headers)
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()["active"] is False
    assert lifted.json()["lifted_by"] == str(admin_user.id)
    again = client.post(f"/api/v1/suppressions/{body['id']}/lift", headers=headers)
    assert again.status_code == 409

    active = client.get("/api/v1/suppressions", headers=headers).json()["items"]
    assert len(active) == 2
    everything = client.get(
        "/api/v1/suppressions", params={"active_only": "false"}, headers=headers
    ).json()["items"]
    assert len(everything) == 3


def test_validation(client: TestClient, admin_user: User) -> None:
    assert add(client, admin_user, reason="nothing to target").status_code == 422
    assert add(client, admin_user, domain="not a domain", reason="x").status_code == 422
    assert add(client, admin_user, phone_e164="12", reason="x").status_code == 422
    assert add(client, admin_user, domain="a.com").status_code == 422, "reason is required"
    missing = add(
        client, admin_user, business_id="00000000-0000-0000-0000-000000000000", reason="x"
    )
    assert missing.status_code == 404


def test_who_may_read_and_who_may_change(client: TestClient, db: Session, admin_user: User) -> None:
    reviewer = make_user(db, Role.reviewer)
    rep = make_user(db, Role.sales_rep)
    crm = make_user(db, Role.crm_manager)
    created = add(client, admin_user, domain="a.com", reason="x").json()

    for reader in (reviewer, crm):
        assert (
            client.get("/api/v1/suppressions", headers=auth_headers(client, reader)).status_code
            == 200
        )
        assert add(client, reader, domain="b.com", reason="x").status_code == 403
        assert (
            client.post(
                f"/api/v1/suppressions/{created['id']}/lift", headers=auth_headers(client, reader)
            ).status_code
            == 403
        )
    assert client.get("/api/v1/suppressions", headers=auth_headers(client, rep)).status_code == 403
    assert client.get("/api/v1/suppressions").status_code == 401
