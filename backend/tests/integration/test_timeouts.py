"""v0.12.1: nothing the worker talks to may make it wait for ever."""

import subprocess
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core import backup, db, redis
from app.core.config import Settings


def settings(**overrides: Any) -> Settings:
    return Settings(jwt_secret=SecretStr("x" * 40), environment="ci", **overrides)


def test_a_statement_over_the_timeout_raises_instead_of_holding_on(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "get_settings", lambda: settings(db_statement_timeout_ms=200))
    connect_args = db.timeout_connect_args(database_url)
    assert connect_args["connect_timeout"] == 10
    engine = create_engine(database_url, connect_args=connect_args)
    try:
        with (
            engine.connect() as connection,
            pytest.raises(OperationalError, match="statement timeout"),
        ):
            connection.execute(text("SELECT pg_sleep(2)"))
    finally:
        engine.dispose()


def test_the_idle_in_transaction_limit_is_set_on_every_connection(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        db, "get_settings", lambda: settings(db_idle_in_transaction_timeout_ms=45_000)
    )
    engine = create_engine(database_url, connect_args=db.timeout_connect_args(database_url))
    try:
        with engine.connect() as connection:
            shown = connection.execute(text("SHOW idle_in_transaction_session_timeout")).scalar()
    finally:
        engine.dispose()
    assert shown == "45s"


def test_only_postgresql_gets_the_limits() -> None:
    assert db.timeout_connect_args("sqlite:///:memory:") == {}


def test_redis_is_built_with_connect_and_read_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        redis,
        "get_settings",
        lambda: settings(
            redis_url="redis://redis.invalid:6379/0",
            redis_socket_connect_timeout_seconds=2.5,
            redis_socket_timeout_seconds=7.0,
        ),
    )
    before = redis._redis
    redis.set_redis(None)
    try:
        client = redis.get_redis()
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["socket_connect_timeout"] == 2.5
        assert kwargs["socket_timeout"] == 7.0
    finally:
        redis.set_redis(before)


def test_a_pg_dump_that_runs_too_long_fails_the_backup(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def hanging(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    config = settings(backup_dir=str(tmp_path), backup_pg_dump_timeout_seconds=90)
    with pytest.raises(backup.BackupError, match="did not finish within 90 s"):
        backup.create_backup(config, runner=hanging)
    assert seen["timeout"] == 90
