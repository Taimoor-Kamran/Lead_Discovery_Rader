"""Search-job CRUD, validation and pagination."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from tests.conftest import auth_headers, geo_payload, make_user


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Austin dental clinics",
        "geo": geo_payload(),
        "industry": "dental",
        "source_ids": [],
    }
    body.update(overrides)
    return body


def test_a_sales_rep_can_create_a_search_job(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.post(
        "/api/v1/search-jobs", json=payload(), headers=auth_headers(client, sales_user)
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Austin dental clinics"
    assert body["status"] == "draft"
    assert body["created_by"] == str(sales_user.id)

    db.expire_all()
    assert "search_job.created" in list(db.scalars(select(AuditLog.action)))


def test_a_reviewer_may_not_create_a_search_job(client: TestClient, db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    response = client.post(
        "/api/v1/search-jobs", json=payload(), headers=auth_headers(client, reviewer)
    )
    assert response.status_code == 403


def test_a_point_and_radius_geo_is_accepted(
    client: TestClient, db: Session, sales_user: User
) -> None:
    body = payload(geo={"lat": 30.26, "lng": -97.74, "radius_m": 5000})
    response = client.post(
        "/api/v1/search-jobs", json=body, headers=auth_headers(client, sales_user)
    )
    assert response.status_code == 201
    assert response.json()["geo"] == {"lat": 30.26, "lng": -97.74, "radius_m": 5000}


def test_an_incomplete_geo_is_rejected(client: TestClient, db: Session, sales_user: User) -> None:
    response = client.post(
        "/api/v1/search-jobs",
        json=payload(geo={"city": "Austin"}),
        headers=auth_headers(client, sales_user),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_get_and_patch_a_search_job(client: TestClient, db: Session, sales_user: User) -> None:
    headers = auth_headers(client, sales_user)
    created = client.post("/api/v1/search-jobs", json=payload(), headers=headers).json()

    fetched = client.get(f"/api/v1/search-jobs/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    patched = client.patch(
        f"/api/v1/search-jobs/{created['id']}", json={"status": "active"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "active"

    db.expire_all()
    assert "search_job.updated" in list(db.scalars(select(AuditLog.action)))


def test_an_unknown_search_job_is_404(client: TestClient, db: Session, sales_user: User) -> None:
    response = client.get(
        "/api/v1/search-jobs/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(client, sales_user),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_listing_requires_authentication(client: TestClient, db: Session) -> None:
    assert client.get("/api/v1/search-jobs").status_code == 401


def test_the_list_pages_with_a_cursor(client: TestClient, db: Session, sales_user: User) -> None:
    headers = auth_headers(client, sales_user)
    for index in range(3):
        client.post("/api/v1/search-jobs", json=payload(name=f"job-{index}"), headers=headers)

    first = client.get("/api/v1/search-jobs?limit=2", headers=headers).json()
    assert len(first["items"]) == 2
    second = client.get(
        f"/api/v1/search-jobs?limit=2&cursor={first['next_cursor']}", headers=headers
    ).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
