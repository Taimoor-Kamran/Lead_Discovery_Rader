"""Login, refresh, logout, /me and the audit trail they leave."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from tests.conftest import TEST_PASSWORD, auth_headers, login, make_user


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def test_login_returns_an_access_token_and_a_refresh_cookie(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    cookie = get_settings().refresh_cookie_name
    assert cookie in response.cookies
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie


def test_login_writes_an_audit_row(client: TestClient, db: Session, sales_user: User) -> None:
    login(client, sales_user)
    db.expire_all()
    assert "auth.login_succeeded" in _actions(db)


def test_a_wrong_password_is_rejected_and_audited(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": sales_user.email, "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    db.expire_all()
    assert "auth.login_failed" in _actions(db)


def test_an_unknown_email_looks_the_same_as_a_wrong_password(
    client: TestClient, db: Session, sales_user: User
) -> None:
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": TEST_PASSWORD}
    )
    wrong = client.post(
        "/api/v1/auth/login", json={"email": sales_user.email, "password": "wrong-password"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_an_inactive_user_cannot_log_in(client: TestClient, db: Session) -> None:
    user = make_user(db, Role.sales_rep)
    user.is_active = False
    db.commit()
    response = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 401


def test_me_returns_the_authenticated_user(client: TestClient, sales_user: User) -> None:
    response = client.get("/api/v1/auth/me", headers=auth_headers(client, sales_user))
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == sales_user.email
    assert body["role"] == Role.sales_rep.value
    assert "password_hash" not in body


def test_me_without_a_token_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_me_with_a_junk_token_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert response.status_code == 401


def test_refresh_issues_a_new_access_token(
    client: TestClient, db: Session, sales_user: User
) -> None:
    client.post("/api/v1/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD})
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    assert response.json()["access_token"]
    db.expire_all()
    assert "auth.token_refreshed" in _actions(db)


def test_refresh_without_a_cookie_is_401(client: TestClient) -> None:
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_an_access_token_cannot_be_used_to_refresh(client: TestClient, sales_user: User) -> None:
    token = login(client, sales_user)
    client.cookies.set(get_settings().refresh_cookie_name, token)
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_logout_clears_the_cookie_and_is_audited(
    client: TestClient, db: Session, sales_user: User
) -> None:
    client.post("/api/v1/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD})
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    db.expire_all()
    assert "auth.logout" in _actions(db)
