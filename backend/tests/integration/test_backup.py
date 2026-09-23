"""Backups (spec v0.8.0 §2): dump, retention, restore into a temp database, verify."""

import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.cli import main
from app.core import backup
from app.core.config import get_settings
from app.modules.auth.models import Role, User
from app.modules.jobs.models import JobRun, JobRunStatus
from tests.conftest import make_user


def _pg_tools_present() -> bool:
    argv = ["pg_dump", "--version"]  # whichever pg_dump is on PATH, like the CLI itself
    try:
        completed = subprocess.run(argv, capture_output=True, check=False, timeout=10)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


pytestmark = pytest.mark.skipif(
    not _pg_tools_present(), reason="pg_dump / pg_restore are not installed on this machine"
)


@pytest.fixture
def backup_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    directory = tmp_path / "backups"
    monkeypatch.setenv("BACKUP_DIR", str(directory))
    monkeypatch.setenv("BACKUP_KEEP", "3")
    get_settings.cache_clear()
    yield directory
    get_settings.cache_clear()


def _temp_database(database_url: str) -> Iterator[str]:
    name = f"restore_check_{uuid.uuid4().hex[:8]}"
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    base, _, _ = database_url.rpartition("/")
    try:
        yield f"{base}/{name}"
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_libpq_url_drops_the_driver_suffix() -> None:
    assert (
        backup.libpq_url("postgresql+psycopg://radar:pw@postgres:5432/radar")
        == "postgresql://radar:pw@postgres:5432/radar"
    )
    assert backup.libpq_url("postgresql://a@b/c") == "postgresql://a@b/c"


def test_create_backup_writes_a_named_dump_and_keeps_only_the_newest(
    db: Session, backup_dir: Path
) -> None:
    make_user(db, Role.admin)
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Every stamp comes from the same `now` as the dump below, never from the real clock.
    # Retention sorts by the timestamp in the filename, so a file seeded "2 days ago" by the
    # wall clock sorts *above* a fixed `now` once today is two days past it — which made this
    # test start failing on a date rather than on a change.
    now = datetime(2026, 9, 21, 2, 0, 0, tzinfo=UTC)
    for days_ago in (5, 4, 3, 2):
        (backup_dir / backup.backup_name(now - timedelta(days=days_ago))).write_bytes(b"old")

    result = backup.create_backup(now=now)

    assert result.file.name == "radar-20260921-020000.dump"
    assert result.file.size_bytes > 0
    assert Path(result.file.path).read_bytes()[:5] == b"PGDMP", "a custom-format dump"
    kept = [f.name for f in backup.list_backups()]
    assert len(kept) == 3, "BACKUP_KEEP=3"
    assert kept[0] == result.file.name
    assert len(result.pruned) == 2
    assert not list(backup_dir.glob(".*.partial"))


def test_restore_puts_the_rows_back_into_another_database(
    db: Session, backup_dir: Path, database_url: str
) -> None:
    make_user(db, Role.admin, email="restored-admin@example.com")
    result = backup.create_backup()

    for scratch_url in _temp_database(database_url):
        backup.restore_backup(result.file.path, database_url=scratch_url)
        engine = create_engine(scratch_url)
        with engine.connect() as connection:
            emails = list(connection.execute(text("SELECT email FROM users")).scalars())
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        engine.dispose()
        assert emails == ["restored-admin@example.com"]
        assert version == backup.alembic_head()


def test_verify_restores_the_newest_dump_into_a_throwaway_database_and_drops_it(
    db: Session, backup_dir: Path, database_url: str
) -> None:
    make_user(db, Role.admin)
    make_user(db, Role.reviewer)
    backup.create_backup(now=datetime(2026, 9, 20, 2, 0, 0, tzinfo=UTC))
    newest = backup.create_backup(now=datetime(2026, 9, 21, 2, 0, 0, tzinfo=UTC))

    result = backup.verify_backup()

    assert result.ok, result.error
    assert result.file == newest.file.name
    assert result.alembic_current == result.alembic_head == backup.alembic_head()
    assert result.counts["users"] == 2
    assert set(result.counts) == set(backup.CORE_TABLES)
    assert result.database is not None and result.database.startswith("radar_verify_")
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        leftover = connection.execute(
            text("SELECT count(*) FROM pg_database WHERE datname = :name"),
            {"name": result.database},
        ).scalar()
    admin.dispose()
    assert leftover == 0, "the throw-away database is dropped again"


def test_verify_reports_a_corrupt_dump_instead_of_raising(db: Session, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "radar-20260921-020000.dump").write_bytes(b"this is not a dump")

    result = backup.verify_backup()

    assert result.ok is False
    assert result.error is not None and "pg_restore failed" in result.error
    assert "postgresql://" not in result.error


def test_verify_with_no_backup_says_so(db: Session, backup_dir: Path) -> None:
    result = backup.verify_backup()
    assert result.ok is False
    assert result.error == "there is no backup to verify"


def test_a_tool_failure_never_shows_the_database_url(
    db: Session, backup_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr=f"connection to {argv[-1]} refused"
        )

    with pytest.raises(backup.BackupError) as exc:
        backup.create_backup(runner=failing)
    message = str(exc.value)
    assert "pg_dump failed" in message
    assert "postgresql://[REDACTED]" in message
    assert get_settings().database_url not in message


# --- the operator commands are job runs ------------------------------------------------------


def test_make_backup_and_backup_verify_are_recorded_as_job_runs(
    db: Session, backup_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_user(db, Role.admin)

    assert main(["backup"]) == 0
    printed = capsys.readouterr().out
    assert "Backup written" in printed
    assert "Copy .env.prod somewhere safe separately" in printed

    assert main(["backup-verify"]) == 0
    assert "Backup verify OK" in capsys.readouterr().out

    db.expire_all()
    runs = {run.kind: run for run in db.scalars(select(JobRun))}
    assert runs["backup"].status is JobRunStatus.done
    assert runs["backup"].result_summary is not None
    assert runs["backup"].result_summary["file"].startswith("radar-")
    assert runs["backup-verify"].status is JobRunStatus.done
    assert runs["backup-verify"].result_summary is not None
    assert runs["backup-verify"].result_summary["ok"] is True
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_restore_refuses_without_the_confirmation_flag(
    db: Session, backup_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["restore", "--file", "/nowhere/radar-x.dump"]) == 2
    assert "Refusing" in capsys.readouterr().out


def test_restore_command_restores_and_reports(
    db: Session,
    backup_dir: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_user(db, Role.admin, email="cli-restore@example.com")
    result = backup.create_backup()

    for scratch_url in _temp_database(database_url):
        monkeypatch.setenv("DATABASE_URL", scratch_url)
        get_settings.cache_clear()
        assert main(["restore", "--file", result.file.path, "--yes"]) == 0
        assert "Restored radar-" in capsys.readouterr().out
        engine = create_engine(scratch_url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 1
        engine.dispose()
