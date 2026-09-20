"""`make seed-admin` creates or promotes exactly one bootstrap admin."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import main
from app.modules.auth.models import Role, User
from app.modules.auth.service import get_user_by_email
from tests.conftest import TEST_PASSWORD, make_user

ADMIN_EMAIL = "bootstrap-admin@example.com"


def test_seed_admin_creates_the_admin(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASSWORD)

    assert main(["seed-admin"]) == 0

    db.expire_all()
    user = get_user_by_email(db, ADMIN_EMAIL)
    assert user is not None
    assert user.role is Role.admin
    assert user.is_active
    assert TEST_PASSWORD not in capsys.readouterr().out


def test_seed_admin_is_idempotent(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASSWORD)

    assert main(["seed-admin"]) == 0
    assert main(["seed-admin"]) == 0

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_seed_admin_promotes_an_existing_user(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = make_user(db, Role.sales_rep, email=ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASSWORD)

    assert main(["seed-admin"]) == 0

    db.expire_all()
    db.refresh(existing)
    assert existing.role is Role.admin


def test_seed_admin_without_an_email_fails_loudly(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    assert main(["seed-admin"]) == 2


def test_an_unknown_command_is_rejected(db: Session) -> None:
    assert main(["not-a-command"]) == 2


# --- seed-demo-users ------------------------------------------------------------------


def test_seed_demo_users_creates_the_four_demo_accounts(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "environment", "development")
    monkeypatch.setattr(get_settings(), "demo_users_password", SecretStr(TEST_PASSWORD))

    assert main(["seed-demo-users"]) == 0
    assert main(["seed-demo-users"]) == 0, "repeatable"

    db.expire_all()
    roles = {
        email: get_user_by_email(db, email).role  # type: ignore[union-attr]
        for email in (
            "reviewer@example.com",
            "rep1@example.com",
            "rep2@example.com",
            "crm@example.com",
        )
    }
    assert roles == {
        "reviewer@example.com": Role.reviewer,
        "rep1@example.com": Role.sales_rep,
        "rep2@example.com": Role.sales_rep,
        "crm@example.com": Role.crm_manager,
    }
    assert db.scalar(select(func.count()).select_from(User)) == 4
    printed = capsys.readouterr().out
    assert TEST_PASSWORD not in printed
    assert "password kept" in printed


def test_seed_demo_users_refuses_outside_development_and_without_a_password(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "demo_users_password", SecretStr(TEST_PASSWORD))
    assert main(["seed-demo-users"]) == 2, "the suite runs as ci"
    assert "local development only" in capsys.readouterr().out

    monkeypatch.setattr(get_settings(), "environment", "development")
    monkeypatch.setattr(get_settings(), "demo_users_password", SecretStr(""))
    assert main(["seed-demo-users"]) == 2
    monkeypatch.setattr(get_settings(), "demo_users_password", SecretStr("short"))
    assert main(["seed-demo-users"]) == 2
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User)) == 0
