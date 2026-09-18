"""`/sources`, and the rule that a search job may only name a source that can run."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from app.modules.sources.models import Source
from tests.conftest import auth_headers, geo_payload, make_user


def synced(db: Session) -> Source:
    sources = registry.sync_sources(db)
    db.commit()
    return next(s for s in sources if s.name == GOOGLE_PLACES)


def job_payload(source_ids: list[str]) -> dict[str, object]:
    return {
        "name": "Austin plumbers",
        "geo": geo_payload(),
        "industry": "plumber",
        "source_ids": source_ids,
    }


def test_any_signed_in_role_can_list_the_sources(
    client: TestClient, db: Session, sales_user: User
) -> None:
    source = synced(db)

    response = client.get("/api/v1/sources", headers=auth_headers(client, sales_user))

    assert response.status_code == 200
    [body] = [s for s in response.json() if s["name"] == GOOGLE_PLACES]
    assert body["id"] == str(source.id)
    assert body["kind"] == "api"
    assert body["enabled"] is True
    assert body["config"]["terms_url"].startswith("https://")


def test_listing_the_sources_requires_authentication(client: TestClient, db: Session) -> None:
    assert client.get("/api/v1/sources").status_code == 401


def test_a_tech_admin_can_disable_a_source(client: TestClient, db: Session) -> None:
    source = synced(db)
    tech_admin = make_user(db, Role.tech_admin)

    response = client.patch(
        f"/api/v1/sources/{source.id}",
        json={"enabled": False},
        headers=auth_headers(client, tech_admin),
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    db.expire_all()
    assert db.get(Source, source.id).enabled is False  # type: ignore[union-attr]
    assert "source.disabled" in list(db.scalars(select(AuditLog.action)))


def test_an_admin_can_enable_a_source_again(
    client: TestClient, db: Session, admin_user: User
) -> None:
    source = synced(db)
    source.enabled = False
    db.commit()

    response = client.patch(
        f"/api/v1/sources/{source.id}",
        json={"enabled": True},
        headers=auth_headers(client, admin_user),
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert "source.enabled" in list(db.scalars(select(AuditLog.action)))


def test_a_sales_rep_may_not_toggle_a_source(
    client: TestClient, db: Session, sales_user: User
) -> None:
    source = synced(db)

    response = client.patch(
        f"/api/v1/sources/{source.id}",
        json={"enabled": False},
        headers=auth_headers(client, sales_user),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_toggling_an_unknown_source_is_404(
    client: TestClient, db: Session, admin_user: User
) -> None:
    response = client.patch(
        f"/api/v1/sources/{uuid.uuid4()}",
        json={"enabled": False},
        headers=auth_headers(client, admin_user),
    )

    assert response.status_code == 404


def test_a_search_job_naming_an_unknown_source_is_rejected(
    client: TestClient, db: Session, sales_user: User
) -> None:
    unknown = str(uuid.uuid4())

    response = client.post(
        "/api/v1/search-jobs",
        json=job_payload([unknown]),
        headers=auth_headers(client, sales_user),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.json()["error"]["details"]["unknown_source_ids"] == [unknown]


def test_a_search_job_naming_a_disabled_source_is_rejected(
    client: TestClient, db: Session, sales_user: User
) -> None:
    source = synced(db)
    source.enabled = False
    db.commit()

    response = client.post(
        "/api/v1/search-jobs",
        json=job_payload([str(source.id)]),
        headers=auth_headers(client, sales_user),
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["disabled_sources"] == [GOOGLE_PLACES]


def test_a_search_job_naming_an_enabled_source_is_accepted(
    client: TestClient, db: Session, sales_user: User
) -> None:
    source = synced(db)

    response = client.post(
        "/api/v1/search-jobs",
        json=job_payload([str(source.id)]),
        headers=auth_headers(client, sales_user),
    )

    assert response.status_code == 201
    assert response.json()["source_ids"] == [str(source.id)]


def test_patching_a_job_onto_a_disabled_source_is_rejected(
    client: TestClient, db: Session, sales_user: User
) -> None:
    source = synced(db)
    headers = auth_headers(client, sales_user)
    job_id = client.post("/api/v1/search-jobs", json=job_payload([]), headers=headers).json()["id"]
    source.enabled = False
    db.commit()

    response = client.patch(
        f"/api/v1/search-jobs/{job_id}",
        json={"source_ids": [str(source.id)]},
        headers=headers,
    )

    assert response.status_code == 422
