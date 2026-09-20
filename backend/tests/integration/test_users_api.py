"""User administration and the RBAC boundary around it."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from tests.conftest import TEST_PASSWORD, auth_headers, make_user

NEW_USER = {
    "email": "new-person@example.com",
    "password": "a-long-enough-password",
    "role": "reviewer",
}


def test_an_admin_can_create_a_user(client: TestClient, db: Session, admin_user: User) -> None:
    response = client.post("/api/v1/users", json=NEW_USER, headers=auth_headers(client, admin_user))
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == NEW_USER["email"]
    assert body["role"] == "reviewer"
    assert body["must_change_password"] is True, "an admin-created password is temporary"
    assert body["locked"] is False and body["last_login_at"] is None
    assert "password" not in body and "password_hash" not in body

    db.expire_all()
    actions = list(db.scalars(select(AuditLog.action)))
    assert "user.created" in actions


def test_a_sales_rep_gets_403_on_create_user(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.post("/api/v1/users", json=NEW_USER, headers=auth_headers(client, sales_user))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_an_unauthenticated_create_user_is_401(client: TestClient) -> None:
    response = client.post("/api/v1/users", json=NEW_USER)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_a_reviewer_gets_403_on_list_users(client: TestClient, db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)
    response = client.get("/api/v1/users", headers=auth_headers(client, reviewer))
    assert response.status_code == 403


def test_duplicate_emails_are_a_conflict(client: TestClient, db: Session, admin_user: User) -> None:
    headers = auth_headers(client, admin_user)
    assert client.post("/api/v1/users", json=NEW_USER, headers=headers).status_code == 201
    second = client.post("/api/v1/users", json=NEW_USER, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_emails_are_case_insensitive(client: TestClient, db: Session, admin_user: User) -> None:
    headers = auth_headers(client, admin_user)
    client.post("/api/v1/users", json=NEW_USER, headers=headers)
    shouty = dict(NEW_USER, email="NEW-PERSON@EXAMPLE.COM")
    assert client.post("/api/v1/users", json=shouty, headers=headers).status_code == 409


def test_a_short_password_is_rejected(client: TestClient, db: Session, admin_user: User) -> None:
    payload = dict(NEW_USER, password="hunter2")
    response = client.post("/api/v1/users", json=payload, headers=auth_headers(client, admin_user))
    assert response.status_code == 422
    # The rejected value must not be echoed back in the error.
    assert "hunter2" not in response.text


def test_an_admin_can_deactivate_a_user(client: TestClient, db: Session, admin_user: User) -> None:
    target = make_user(db, Role.sales_rep)
    response = client.patch(
        f"/api/v1/users/{target.id}",
        json={"is_active": False},
        headers=auth_headers(client, admin_user),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    login = client.post(
        "/api/v1/auth/login", json={"email": target.email, "password": TEST_PASSWORD}
    )
    assert login.status_code == 401


def test_patching_an_unknown_user_is_404(client: TestClient, db: Session, admin_user: User) -> None:
    response = client.patch(
        "/api/v1/users/00000000-0000-0000-0000-000000000000",
        json={"is_active": False},
        headers=auth_headers(client, admin_user),
    )
    assert response.status_code == 404


def test_the_user_list_pages_with_a_cursor(
    client: TestClient, db: Session, admin_user: User
) -> None:
    for index in range(4):
        make_user(db, Role.sales_rep, email=f"paged-{index}@example.com")
    headers = auth_headers(client, admin_user)

    first = client.get("/api/v1/users?limit=2", headers=headers).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]

    second = client.get(
        f"/api/v1/users?limit=2&cursor={first['next_cursor']}", headers=headers
    ).json()
    assert len(second["items"]) == 2

    seen = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(seen)) == 4


def test_a_broken_cursor_is_a_422(client: TestClient, db: Session, admin_user: User) -> None:
    response = client.get(
        "/api/v1/users?cursor=not-a-cursor", headers=auth_headers(client, admin_user)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
