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
