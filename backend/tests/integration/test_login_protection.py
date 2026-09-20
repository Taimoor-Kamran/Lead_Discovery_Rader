"""Login protection (spec v0.8.0 §5): 6th bad login within 15 min → 429; 10 failures →
locked 15 min; every failure, lock and rate-limit is audited; unlock from the API."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.audit.models import AuditLog
from app.modules.auth import service
from app.modules.auth.models import Role, User
from tests.conftest import TEST_PASSWORD, auth_headers, make_user

API = "/api/v1"


@pytest.fixture
def app_client(db: Session) -> Iterator[TestClient]:
    """Like `client`, but with a fixed client address so the IP counter is deterministic."""
    from app.main import create_app

    with TestClient(
        create_app(), raise_server_exceptions=False, client=("203.0.113.10", 40000)
    ) as test_client:
        yield test_client


def _client_from(db: Session, ip: str) -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False, client=(ip, 40000))


def _login(test_client: TestClient, email: str, password: str) -> int:
    response = test_client.post(f"{API}/auth/login", json={"email": email, "password": password})
    status: int = response.status_code
    return status


def _actions(db: Session) -> list[str]:
    db.expire_all()
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def test_the_sixth_bad_login_from_one_address_is_rate_limited(
    app_client: TestClient, db: Session, sales_user: User
) -> None:
    for _ in range(5):
        assert _login(app_client, sales_user.email, "wrong-password") == 401

    response = app_client.post(
        f"{API}/auth/login", json={"email": sales_user.email, "password": "wrong-password"}
    )
    assert response.status_code == 429
    body = response.json()["error"]
    assert body["code"] == "rate_limited"
    assert "15 minutes" in body["message"]

    # Even the right password is refused while the window is open.
    assert _login(app_client, sales_user.email, TEST_PASSWORD) == 429

    actions = _actions(db)
    assert actions.count("auth.login_failed") == 5
    assert actions.count("auth.login_rate_limited") == 2


def test_the_rate_limit_is_per_email_and_address(
    app_client: TestClient, db: Session, sales_user: User
) -> None:
    other = make_user(db, Role.reviewer)
    for _ in range(5):
        _login(app_client, sales_user.email, "wrong-password")

    assert _login(app_client, other.email, TEST_PASSWORD) == 200, "another email is not blocked"
    with _client_from(db, "198.51.100.7") as elsewhere:
        assert _login(elsewhere, sales_user.email, TEST_PASSWORD) == 200, "another address is not"


def test_an_unknown_email_is_rate_limited_the_same_way(app_client: TestClient, db: Session) -> None:
    for _ in range(5):
        assert _login(app_client, "nobody@example.com", "wrong-password") == 401
    assert _login(app_client, "nobody@example.com", "wrong-password") == 429


def test_the_window_expires(
    app_client: TestClient, db: Session, sales_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOGIN_WINDOW_MINUTES", "1")
    get_settings.cache_clear()
    for _ in range(5):
        _login(app_client, sales_user.email, "wrong-password")
    assert _login(app_client, sales_user.email, TEST_PASSWORD) == 429

    from app.core import redis as redis_module

    key = service._failure_key(sales_user.email, "203.0.113.10")
    ttl = cast(int, redis_module.get_redis().ttl(key))
    assert 0 < int(ttl) <= 60
    redis_module.get_redis().delete(key)  # what the clock would do a minute later
    assert _login(app_client, sales_user.email, TEST_PASSWORD) == 200


def test_ten_failures_lock_the_account_for_fifteen_minutes(db: Session, sales_user: User) -> None:
    """Failures from several addresses count against the account itself."""
    addresses = [f"203.0.113.{n}" for n in range(1, 11)]
    for ip in addresses:
        with _client_from(db, ip) as test_client:
            assert _login(test_client, sales_user.email, "wrong-password") == 401

    db.expire_all()
    assert sales_user.locked_until is not None
    assert sales_user.locked_until - datetime.now(UTC) > timedelta(minutes=14)
    assert sales_user.failed_login_count == 0

    with _client_from(db, "203.0.113.99") as fresh_address:
        response = fresh_address.post(
            f"{API}/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD}
        )
    assert response.status_code == 401, "locked: even the right password is refused"
    assert response.json()["error"]["message"] == service.INVALID_CREDENTIALS

    actions = _actions(db)
    assert actions.count("auth.login_failed") == 11
    assert actions.count("auth.account_locked") == 1
    locked_row = db.scalars(
        select(AuditLog).where(AuditLog.action == "auth.login_failed").order_by(AuditLog.id.desc())
    ).first()
    assert locked_row is not None and locked_row.after == {
        "reason": "locked",
        "client": "203.0.113.99",
    }


def test_a_lock_expires_and_a_success_resets_the_counters(
    app_client: TestClient, db: Session, sales_user: User
) -> None:
    sales_user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    sales_user.failed_login_count = 4
    db.commit()

    assert _login(app_client, sales_user.email, TEST_PASSWORD) == 200

    db.expire_all()
    assert sales_user.locked_until is None
    assert sales_user.failed_login_count == 0
    assert sales_user.last_login_at is not None


def test_an_admin_can_unlock_from_the_api(
    client: TestClient, db: Session, admin_user: User, sales_user: User
) -> None:
    sales_user.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    sales_user.failed_login_count = 3
    db.commit()
    headers = auth_headers(client, admin_user)

    listed = client.get(f"{API}/users", headers=headers).json()["items"]
    row = next(item for item in listed if item["id"] == str(sales_user.id))
    assert row["locked"] is True

    response = client.post(f"{API}/users/{sales_user.id}/unlock", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["locked"] is False
    assert response.json()["locked_until"] is None
    assert "user.unlocked" in _actions(db)

    login = client.post(
        f"{API}/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200


def test_only_admins_unlock(client: TestClient, db: Session, sales_user: User) -> None:
    reviewer = make_user(db, Role.reviewer)
    response = client.post(
        f"{API}/users/{sales_user.id}/unlock", headers=auth_headers(client, reviewer)
    )
    assert response.status_code == 403
