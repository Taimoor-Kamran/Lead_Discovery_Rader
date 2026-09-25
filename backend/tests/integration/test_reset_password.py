"""`reset-password`: a new password, and every token the user already held retired.

A reset that left old sessions working would be no reset at all, so the tests that matter
are the ones proving a token minted before the reset stops being accepted.
"""

import getpass
import os
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli
from app.core.errors import NotFoundError, ValidationFailedError
from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from app.modules.auth.service import reset_password
from tests.conftest import TEST_PASSWORD, auth_headers, login, make_user

NEW_PASSWORD = "a-brand-new-passphrase"


@pytest.fixture
def non_interactive() -> Iterator[None]:
    """`NEW_PASSWORD` is the documented way to run the command without a terminal."""
    previous = os.environ.get("NEW_PASSWORD")
    os.environ["NEW_PASSWORD"] = NEW_PASSWORD
    yield
    if previous is None:
        os.environ.pop("NEW_PASSWORD", None)
    else:
        os.environ["NEW_PASSWORD"] = previous


# --- the service ---------------------------------------------------------------------


def test_the_password_changes_and_the_token_version_moves(db: Session, sales_user: User) -> None:
    before = sales_user.token_version
    original_hash = sales_user.password_hash

    user = reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    assert user.token_version == before + 1
    assert user.password_hash != original_hash
    assert NEW_PASSWORD not in user.password_hash, "the password is hashed, never stored"


def test_a_short_password_is_refused_before_anything_is_written(
    db: Session, sales_user: User
) -> None:
    original = sales_user.password_hash

    with pytest.raises(ValidationFailedError):
        reset_password(db, sales_user.email, "short")
    db.rollback()

    assert db.get(User, sales_user.id).password_hash == original  # type: ignore[union-attr]


def test_an_unknown_email_is_not_found(db: Session) -> None:
    with pytest.raises(NotFoundError):
        reset_password(db, "nobody@example.com", NEW_PASSWORD)


def test_a_reset_writes_an_audit_entry_with_no_actor(db: Session, sales_user: User) -> None:
    reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.password_reset")).one()
    assert entry.actor_id is None, "a terminal has no authenticated actor"
    assert entry.entity_id == str(sales_user.id)
    assert entry.after is not None
    assert entry.after["via"] == "cli"


def test_the_new_password_is_never_in_the_audit_entry(db: Session, sales_user: User) -> None:
    reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.password_reset")).one()
    assert NEW_PASSWORD not in str(entry.after)


# --- what it does to sessions --------------------------------------------------------


def test_an_access_token_issued_before_the_reset_stops_working(
    client: TestClient, db: Session, sales_user: User
) -> None:
    headers = auth_headers(client, sales_user)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_revoked"


def test_a_refresh_token_issued_before_the_reset_stops_working(
    client: TestClient, db: Session, sales_user: User
) -> None:
    login(client, sales_user)  # sets the refresh cookie on the client
    assert client.post("/api/v1/auth/refresh").status_code == 200

    reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_revoked"


def test_the_new_password_logs_in_and_the_old_one_does_not(
    client: TestClient, db: Session, sales_user: User
) -> None:
    reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    old = client.post(
        "/api/v1/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD}
    )
    new = client.post(
        "/api/v1/auth/login", json={"email": sales_user.email, "password": NEW_PASSWORD}
    )

    assert old.status_code == 401
    assert new.status_code == 200


def test_a_token_issued_after_the_reset_works(
    client: TestClient, db: Session, sales_user: User
) -> None:
    reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    headers = {"Authorization": f"Bearer {login(client, sales_user, NEW_PASSWORD)}"}

    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200


# --- the command ---------------------------------------------------------------------


def test_the_command_resets_the_password(
    db: Session, sales_user: User, non_interactive: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["reset-password", "--email", sales_user.email])
    db.expire_all()

    assert code == 0
    assert db.get(User, sales_user.id).token_version == 2  # type: ignore[union-attr]
    assert NEW_PASSWORD not in capsys.readouterr().out, "a password is never echoed"


def test_the_command_rejects_a_short_password(
    db: Session, sales_user: User, capsys: pytest.CaptureFixture[str]
) -> None:
    os.environ["NEW_PASSWORD"] = "too-short"
    try:
        code = cli.main(["reset-password", "--email", sales_user.email])
    finally:
        os.environ.pop("NEW_PASSWORD", None)
    db.expire_all()

    assert code == 2
    assert "at least 12 characters" in capsys.readouterr().out
    assert db.get(User, sales_user.id).token_version == 1  # type: ignore[union-attr]


def test_the_command_reports_an_unknown_email(
    db: Session, non_interactive: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["reset-password", "--email", "nobody@example.com"])

    assert code == 2
    assert "No user has the email" in capsys.readouterr().out


class _Stdin:
    """Stands in for `sys.stdin`: a terminal or not, and nothing to read either way."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_without_new_password_or_a_terminal_the_command_says_why_and_changes_nothing(
    db: Session,
    sales_user: User,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # What `NEW_PASSWORD=... make reset-password` did before v0.11.2: the variable never
    # reached the container, and getpass died with a bare EOFError.
    monkeypatch.delenv("NEW_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=False))

    def no_prompt(prompt: str = "") -> str:
        raise AssertionError("getpass must not be reached without a terminal")

    monkeypatch.setattr(getpass, "getpass", no_prompt)

    code = cli.main(["reset-password", "--email", sales_user.email])
    db.expire_all()

    assert code == 2
    out = capsys.readouterr().out
    assert "NEW_PASSWORD is not set" in out
    assert "-e NEW_PASSWORD" in out, "names the fix for a bare docker compose run"
    assert "Nothing was changed" in out
    assert db.get(User, sales_user.id).token_version == 1  # type: ignore[union-attr]


def test_at_a_terminal_without_new_password_the_command_still_prompts(
    db: Session, sales_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEW_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    prompts: list[str] = []

    def typed(prompt: str = "") -> str:
        prompts.append(prompt)
        return NEW_PASSWORD

    monkeypatch.setattr(getpass, "getpass", typed)

    code = cli.main(["reset-password", "--email", sales_user.email])
    db.expire_all()

    assert code == 0
    assert prompts == ["New password: ", "Repeat new password: "]
    assert db.get(User, sales_user.id).token_version == 2  # type: ignore[union-attr]


def test_the_command_is_listed_in_the_usage_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "reset-password" in capsys.readouterr().out


def test_seed_admin_warns_that_a_generated_password_is_shown_once(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    previous = {name: os.environ.get(name) for name in ("ADMIN_EMAIL", "ADMIN_PASSWORD")}
    os.environ["ADMIN_EMAIL"] = "bootstrap-admin@example.com"
    os.environ.pop("ADMIN_PASSWORD", None)
    try:
        assert cli.main(["seed-admin"]) == 0
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    out = capsys.readouterr().out
    assert "ONCE" in out
    assert "reset-password" in out


def test_a_reset_leaves_other_users_alone(
    client: TestClient, db: Session, sales_user: User
) -> None:
    other = make_user(db, Role.reviewer)
    other_headers = auth_headers(client, other)

    reset_password(db, sales_user.email, NEW_PASSWORD)
    db.commit()

    assert client.get("/api/v1/auth/me", headers=other_headers).status_code == 200
