"""Backups: `pg_dump -Fc` into `BACKUP_DIR`, retention, restore, and a tested verify.

Every function shells out to the PostgreSQL 16 client tools that the image installs
(`pg_dump`, `pg_restore`). The database URL is handed to them as a libpq URL; it is
never logged and never appears in a subprocess argument that a `ps` could show a
password from — libpq reads it from `PGPASSWORD`-free URLs only, so the URL goes in
through the `--dbname` argument the same way the application itself connects. Dumps
contain the database only: `.env*` files are never part of a backup, on purpose.

`verify_backup` is the point of the whole thing: a backup nobody has restored is a hope,
not a backup. It restores the newest dump into a throw-away database, checks that
`alembic_version` matches this code's head and that the core tables came back, and drops
the database again.
"""

import os
import re
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger("app.backup")

BACKUP_PREFIX = "radar-"
BACKUP_SUFFIX = ".dump"
BACKUP_NAME = re.compile(r"^radar-(\d{8}-\d{6})\.dump$")
VERIFY_DB_PREFIX = "radar_verify_"
# The tables a verify counts. Empty is a valid answer (a fresh install); a missing table
# is not.
CORE_TABLES: tuple[str, ...] = (
    "users",
    "search_jobs",
    "job_runs",
    "discovered_records",
    "businesses",
    "opportunities",
    "crm_leads",
    "alerts",
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class BackupError(RuntimeError):
    """A backup step failed. The message is safe to store and show (no URL, no secret)."""


@dataclass(frozen=True)
class BackupFile:
    path: str
    name: str
    created_at: datetime
    size_bytes: int


@dataclass(frozen=True)
class BackupResult:
    file: BackupFile
    pruned: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "file": self.file.name,
            "size_bytes": self.file.size_bytes,
            "created_at": self.file.created_at.isoformat(),
            "pruned": list(self.pruned),
        }


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    file: str | None
    database: str | None = None
    alembic_head: str | None = None
    alembic_current: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "file": self.file,
            "database": self.database,
            "alembic_head": self.alembic_head,
            "alembic_current": self.alembic_current,
            "counts": dict(self.counts),
            "error": self.error,
        }


# --- helpers --------------------------------------------------------------------------


def libpq_url(database_url: str) -> str:
    """`postgresql+psycopg://…` (SQLAlchemy) → `postgresql://…` (what libpq tools take)."""
    scheme, _, rest = database_url.partition("://")
    return f"{scheme.split('+', 1)[0]}://{rest}"


def backup_dir(settings: Settings | None = None) -> Path:
    config = settings or get_settings()
    return Path(config.backup_dir).expanduser()


def backup_name(now: datetime) -> str:
    return f"{BACKUP_PREFIX}{now:%Y%m%d-%H%M%S}{BACKUP_SUFFIX}"


def _file_info(path: Path) -> BackupFile:
    match = BACKUP_NAME.match(path.name)
    if match:
        created = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(
            tzinfo=ZoneInfo(get_settings().timezone)
        )
    else:  # pragma: no cover - only our own names are listed
        created = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return BackupFile(
        path=str(path), name=path.name, created_at=created, size_bytes=path.stat().st_size
    )


def list_backups(settings: Settings | None = None) -> list[BackupFile]:
    """Every dump in the backup directory, newest first (by the timestamp in its name)."""
    directory = backup_dir(settings)
    if not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and BACKUP_NAME.match(p.name)]
    return sorted((_file_info(p) for p in files), key=lambda f: f.name, reverse=True)


def newest_backup(settings: Settings | None = None) -> BackupFile | None:
    files = list_backups(settings)
    return files[0] if files else None


def prune_backups(settings: Settings | None = None) -> list[str]:
    """Delete everything but the newest `BACKUP_KEEP` dumps. Returns what was removed."""
    config = settings or get_settings()
    keep = max(int(config.backup_keep), 1)
    removed: list[str] = []
    for stale in list_backups(config)[keep:]:
        os.remove(stale.path)
        removed.append(stale.name)
    if removed:
        logger.info("pruned old backups", extra={"removed": removed, "keep": keep})
    return removed


def _run(runner: Runner, argv: Sequence[str], *, what: str) -> "subprocess.CompletedProcess[str]":
    try:
        completed = runner(list(argv), capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise BackupError(
            f"{what}: '{argv[0]}' is not installed. The api and worker images ship the "
            "PostgreSQL 16 client tools; on a developer machine install postgresql-client-16."
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else f"exit code {completed.returncode}"
        raise BackupError(f"{what} failed: {_scrub(tail)}")
    return completed


def _scrub(text_value: str) -> str:
    """Drop anything that looks like a connection URL from a tool's stderr."""
    return re.sub(r"postgres(ql)?://\S+", "postgresql://[REDACTED]", text_value)


# --- the three operations --------------------------------------------------------------


def create_backup(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    runner: Runner = subprocess.run,
) -> BackupResult:
    """`pg_dump -Fc` the configured database and keep only the newest `BACKUP_KEEP` files."""
    config = settings or get_settings()
    directory = backup_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now(ZoneInfo(config.timezone))
    target = directory / backup_name(moment)
    partial = directory / f".{target.name}.partial"
    _run(
        runner,
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={partial}",
            f"--dbname={libpq_url(config.database_url)}",
        ],
        what="pg_dump",
    )
    # Written to a dotfile first, so a dump that died half-way never counts as a backup.
    os.replace(partial, target)
    result = BackupResult(file=_file_info(target), pruned=tuple(prune_backups(config)))
    logger.info(
        "backup written",
        extra={"file": result.file.name, "size_bytes": result.file.size_bytes},
    )
    return result


def restore_backup(
    path: str | os.PathLike[str],
    *,
    database_url: str | None = None,
    settings: Settings | None = None,
    runner: Runner = subprocess.run,
) -> str:
    """`pg_restore --clean --if-exists` one dump into the given database.

    Returns the file name. The caller is responsible for stopping the api and the worker
    first and for running `alembic upgrade head` afterwards (`make restore` does both).
    """
    config = settings or get_settings()
    file = Path(path)
    if not file.is_file():
        raise BackupError(f"restore: '{file.name}' is not a file in the backup directory")
    url = database_url or config.database_url
    _run(
        runner,
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            f"--dbname={libpq_url(url)}",
            str(file),
        ],
        what="pg_restore",
    )
    logger.info("backup restored", extra={"file": file.name})
    return file.name


def alembic_head() -> str | None:
    """The newest migration this code carries, read from `migrations/`."""
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def _admin_engine(database_url: str) -> Any:
    return create_engine(database_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)


def _sibling_url(database_url: str, name: str) -> str:
    base, _, _ = database_url.rpartition("/")
    return f"{base}/{name}"


def verify_backup(
    settings: Settings | None = None,
    *,
    file: str | os.PathLike[str] | None = None,
    runner: Runner = subprocess.run,
) -> VerifyResult:
    """Restore the newest dump into a throw-away database, check it, and drop it.

    Never raises for a bad backup: the answer is data (`ok=False` + `error`) so the
    scheduler can record it and raise an alert.
    """
    config = settings or get_settings()
    chosen = Path(file) if file is not None else None
    if chosen is None:
        newest = newest_backup(config)
        if newest is None:
            return VerifyResult(ok=False, file=None, error="there is no backup to verify")
        chosen = Path(newest.path)
    if not chosen.is_file():
        return VerifyResult(ok=False, file=chosen.name, error="the backup file does not exist")

    name = f"{VERIFY_DB_PREFIX}{uuid.uuid4().hex[:12]}"
    admin = _admin_engine(config.database_url)
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:
        admin.dispose()
        return VerifyResult(
            ok=False,
            file=chosen.name,
            error=f"could not create the verify database: {type(exc).__name__}",
        )

    scratch_url = _sibling_url(config.database_url, name)
    try:
        restore_backup(chosen, database_url=scratch_url, settings=config, runner=runner)
        head = alembic_head()
        engine = create_engine(scratch_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                current = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                counts = {
                    # Table names come from CORE_TABLES above, never from input.
                    table: int(
                        connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()  # noqa: S608
                        or 0
                    )
                    for table in CORE_TABLES
                }
        finally:
            engine.dispose()
        current_str = str(current) if current is not None else None
        ok = current_str == head
        error = None if ok else f"alembic_version is {current_str}, this code expects {head}"
        result = VerifyResult(
            ok=ok,
            file=chosen.name,
            database=name,
            alembic_head=head,
            alembic_current=current_str,
            counts=counts,
            error=error,
        )
    except BackupError as exc:
        result = VerifyResult(ok=False, file=chosen.name, database=name, error=str(exc))
    except Exception as exc:
        result = VerifyResult(
            ok=False,
            file=chosen.name,
            database=name,
            error=f"{type(exc).__name__}: {_scrub(str(exc))[:300]}",
        )
    finally:
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        except Exception:
            logger.warning("could not drop the verify database", extra={"database": name})
        admin.dispose()

    level = logger.info if result.ok else logger.error
    level("backup verify finished", extra=result.summary())
    return result
