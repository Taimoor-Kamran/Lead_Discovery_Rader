"""Forced password change (spec v0.8.0 §5): a user an admin creates or resets can only
change their password until they have; the rules; own-password change from /profile."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from app.modules.auth.passwords import password_problems
from tests.conftest import TEST_PASSWORD, auth_headers, login, make_user

API = "/api/v1"
TEMP_PASSWORD = "temporary-pass-9f3k2"
NEW_PASSWORD = "a-brand-new-passphrase-42"


def _token(test_client: TestClient, email: str, password: str) -> str:
    response = test_client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _create(test_client: TestClient, admin: User, email: str) -> dict[str, object]:
    response = test_client.post(
        f"{API}/users",
        json={"email": email, "password": TEMP_PASSWORD, "role": "reviewer"},
        headers=auth_headers(test_client, admin),
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


# --- the rules -----------------------------------------------------------------------------


def test_the_password_rules() -> None:
    assert password_problems("a-long-enough-password", email="x@example.com") == []
    assert password_problems("short", email="x@example.com") == [
        "it must be at least 12 characters long"
    ]
    assert "it must not be your email address" in password_problems(
        "Reviewer@Example.com", email="reviewer@example.com"
    )
    assert "it must not be your email address" in password_problems(
        "longlocalpart", email="longlocalpart@example.com"
    )
    assert any(
        "most common" in problem for problem in password_problems("password1234", email=None)
    )
    assert any("most common" in problem for problem in password_problems("Welcome12345"))


def test_a_common_or_email_password_is_refused_by_the_api(
    client: TestClient, db: Session, admin_user: User
) -> None:
    headers = auth_headers(client, admin_user)
    for bad in ("password1234", "weak-person@example.com"):
        response = client.post(
            f"{API}/users",
            json={"email": "weak-person@example.com", "password": bad, "role": "reviewer"},
            headers=headers,
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "weak_password"
        assert bad not in response.text


# --- the forced change ------------------------------------------------------------------------


def test_a_user_an_admin_creates_must_change_their_password_first(
    client: TestClient, db: Session, admin_user: User
) -> None:
    created = _create(client, admin_user, "fresh@example.com")
    assert created["must_change_password"] is True

    token = _token(client, "fresh@example.com", TEMP_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["must_change_password"] is True

    blocked = client.get(f"{API}/review-queue", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "password_change_required"
    assert client.get(f"{API}/leads", headers=headers).status_code == 403

    # The change itself is allowed, and hands back a working session.
    changed = client.post(
        f"{API}/auth/change-password",
        json={"current_password": TEMP_PASSWORD, "new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    new_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert client.get(f"{API}/review-queue", headers=new_headers).status_code == 200
    assert client.get(f"{API}/auth/me", headers=new_headers).json()["must_change_password"] is False

    # The old token died with the old password; the old password no longer signs in.
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401
    old = client.post(
        f"{API}/auth/login", json={"email": "fresh@example.com", "password": TEMP_PASSWORD}
    )
    assert old.status_code == 401
    assert _token(client, "fresh@example.com", NEW_PASSWORD)

    db.expire_all()
    actions = list(db.scalars(select(AuditLog.action)))
    assert "user.password_changed" in actions


def test_the_change_needs_the_current_password_and_a_different_valid_new_one(
    client: TestClient, db: Session, sales_user: User
) -> None:
    headers = auth_headers(client, sales_user)

    wrong = client.post(
        f"{API}/auth/change-password",
        json={"current_password": "not-my-password", "new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_credentials"

    same = client.post(
        f"{API}/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
        headers=headers,
    )
    assert same.status_code == 422 and same.json()["error"]["code"] == "weak_password"

    short = client.post(
        f"{API}/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": "tiny"},
        headers=headers,
    )
    assert short.status_code == 422
    assert "tiny" not in short.text

    db.expire_all()
    assert "auth.password_change_failed" in list(db.scalars(select(AuditLog.action)))


def test_an_admin_reset_forces_a_change_and_signs_the_user_out_everywhere(
    client: TestClient, db: Session, admin_user: User, sales_user: User
) -> None:
    old_token = login(client, sales_user)
    old_headers = {"Authorization": f"Bearer {old_token}"}
    assert client.get(f"{API}/leads", headers=old_headers).status_code == 200

    response = client.post(
        f"{API}/users/{sales_user.id}/reset-password",
        json={"password": TEMP_PASSWORD},
        headers=auth_headers(client, admin_user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["must_change_password"] is True
    assert TEMP_PASSWORD not in response.text

    assert client.get(f"{API}/leads", headers=old_headers).status_code == 401, "signed out"
    token = _token(client, sales_user.email, TEMP_PASSWORD)
    assert (
        client.get(f"{API}/leads", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    )

    db.expire_all()
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action == "user.password_reset")))
    assert rows and rows[-1].actor_id == admin_user.id
    assert rows[-1].after is not None and rows[-1].after["via"] == "admin"


def test_only_admins_reset_passwords(client: TestClient, db: Session, sales_user: User) -> None:
    reviewer = make_user(db, Role.reviewer)
    response = client.post(
        f"{API}/users/{sales_user.id}/reset-password",
        json={"password": TEMP_PASSWORD},
        headers=auth_headers(client, reviewer),
    )
    assert response.status_code == 403


def test_a_patch_with_a_password_is_a_reset_too(
    client: TestClient, db: Session, admin_user: User, sales_user: User
) -> None:
    response = client.patch(
        f"{API}/users/{sales_user.id}",
        json={"password": TEMP_PASSWORD},
        headers=auth_headers(client, admin_user),
    )
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True


def test_logout_still_works_while_a_change_is_pending(
    client: TestClient, db: Session, admin_user: User
) -> None:
    _create(client, admin_user, "leaving@example.com")
    token = _token(client, "leaving@example.com", TEMP_PASSWORD)
    response = client.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204
