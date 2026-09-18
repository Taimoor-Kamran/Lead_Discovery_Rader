"""Shared fixtures.

Integration tests run against a real PostgreSQL 16 started by testcontainers, because the
schema uses citext, JSONB, arrays, native enums and a trigger — none of which SQLite has.
Redis is faked; no test ever touches a live external service.
"""

import os
import subprocess
import uuid
from collections.abc import Iterator
from typing import Any

import fakeredis
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from rq import Queue
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import redis as redis_module
from app.core.config import get_settings
from app.core.db import get_session_factory, reset_engine
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import UserCreate
from app.modules.auth.service import create_user

TEST_JWT_SECRET = "test-jwt-secret-value-not-used-anywhere-else"
TEST_PASSWORD = "correct-horse-battery-staple"

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"],  # noqa: S607
                capture_output=True,
                timeout=20,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway PostgreSQL 16 instance for the whole test session."""
    if not _docker_available():
        pytest.skip("Docker is not available; integration tests need a real PostgreSQL")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session", autouse=True)
def _base_environment() -> Iterator[None]:
    """Settings that every test shares. Secrets here are throwaway test values."""
    previous = dict(os.environ)
    os.environ.update(
        {
            "ENVIRONMENT": "ci",
            "JWT_SECRET": TEST_JWT_SECRET,
            "REFRESH_COOKIE_SECURE": "false",
            "JOB_QUEUE_IS_ASYNC": "true",
            "JOB_MAX_ATTEMPTS": "3",
            "JOB_BACKOFF_BASE_SECONDS": "2",
            "LOG_LEVEL": "INFO",
        }
    )
    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(previous)
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated_database(database_url: str, _base_environment: None) -> Iterator[str]:
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    reset_engine()
    command.upgrade(alembic_config(database_url), "head")
    yield database_url


@pytest.fixture
def db(migrated_database: str) -> Iterator[Session]:
    """A session against the migrated database, with every table emptied first."""
    reset_engine()
    session = get_session_factory()()
    session.execute(
        text("TRUNCATE audit_logs, job_runs, search_jobs, sources, users RESTART IDENTITY CASCADE")
    )
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def fake_redis() -> Iterator[fakeredis.FakeStrictRedis]:
    """Swap Redis for an in-process fake. Jobs are queued but never auto-executed."""
    client = fakeredis.FakeStrictRedis()
    redis_module.set_redis(client)
    redis_module.set_queue(Queue("default", connection=client, is_async=True))
    yield client
    redis_module.set_redis(None)
    redis_module.set_queue(None)


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def make_user(session: Session, role: Role, email: str | None = None) -> User:
    address = email or f"{role.value}-{uuid.uuid4().hex[:8]}@example.com"
    user = create_user(
        session, UserCreate(email=address, password=TEST_PASSWORD, role=role, is_active=True)
    )
    session.commit()
    return user


@pytest.fixture
def admin_user(db: Session) -> User:
    return make_user(db, Role.admin)


@pytest.fixture
def sales_user(db: Session) -> User:
    return make_user(db, Role.sales_rep)


def login(test_client: TestClient, user: User, password: str = TEST_PASSWORD) -> str:
    response = test_client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": password}
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def auth_headers(test_client: TestClient, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(test_client, user)}"}


def geo_payload() -> dict[str, Any]:
    return {"city": "Austin", "state": "TX"}
