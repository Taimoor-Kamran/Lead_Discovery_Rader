"""Shared fixtures.

Integration tests run against a real PostgreSQL 16 started by testcontainers, because the
schema uses citext, JSONB, arrays, native enums and a trigger — none of which SQLite has.
Redis is faked; no test ever touches a live external service.
"""

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from typing import Any

import fakeredis
import pytest
import respx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from rq import Queue
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import redis as redis_module
from app.core.config import get_settings
from app.core.db import get_session_factory, reset_engine
from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import UserCreate
from app.modules.auth.service import create_user

TEST_JWT_SECRET = "test-jwt-secret-value-not-used-anywhere-else"
TEST_PASSWORD = "correct-horse-battery-staple"
# A distinctive literal, so a leak into a log line, an error or a stored payload is
# unmistakable rather than a judgement call.
TEST_PLACES_API_KEY = "places-key-SENTINEL-8f2a1c-never-log-me"

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(BACKEND_DIR, "tests", "fixtures")


def load_fixture(*parts: str) -> Any:
    """Read a recorded API response. No test ever calls a live service."""
    with open(os.path.join(FIXTURE_DIR, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def places_fixture(name: str) -> Any:
    return load_fixture("google_places", name)


class FakeClock:
    """A clock that only advances when something sleeps on it.

    Lets a test assert an exact backoff or rate-limit schedule instead of waiting it out,
    while the token bucket still refills correctly as simulated time passes.
    """

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start
        self.delays: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.now += seconds


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
            "GOOGLE_PLACES_API_KEY": TEST_PLACES_API_KEY,
            "PLACES_MAX_RESULTS_PER_JOB": "60",
            "PLACES_DAILY_CALL_CAP": "200",
            "PLACES_RPS": "5",
            "PLACES_CONTENT_TTL_DAYS": "30",
            "REVIEW_UNDO_WINDOW_MINUTES": "30",
            "REVIEW_COOLDOWN_DAYS": "90",
            "REVIEW_WEAK_CONFIDENCE": "0.4",
            "CRM_AUTO_SYNC": "true",
            "APP_BASE_URL": "http://localhost:3000",
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
        text(
            "TRUNCATE alerts, crm_sync_attempts, crm_lead_opportunities, crm_leads, "
            "crm_fake_records, review_decisions, suppressions, opportunities, ai_classifications, "
            "website_audits, api_calls, "
            "match_candidates, business_field_values, businesses, record_sightings, "
            "discovered_records, audit_logs, job_runs, search_jobs, sources, users "
            "RESTART IDENTITY CASCADE"
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _settings_cache() -> Iterator[None]:
    """A test that changes the environment must not leave its settings cached for the next.

    Autouse fixtures are set up first and torn down last, so this runs after `monkeypatch`
    has put the environment back.
    """
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fake_redis() -> Iterator[fakeredis.FakeStrictRedis]:
    """Swap Redis for an in-process fake. Jobs are queued but never auto-executed."""
    client = fakeredis.FakeStrictRedis()
    redis_module.set_redis(client)
    redis_module.set_queue(Queue("default", connection=client, is_async=True))
    yield client
    redis_module.set_redis(None)
    redis_module.set_queue(None)


@pytest.fixture(autouse=True)
def mock_http() -> Iterator[respx.MockRouter]:
    """Intercept every outbound HTTP call.

    respx patches httpcore, so the ASGI TestClient is untouched but anything that would
    really leave the machine raises instead. A test that needs a response registers a
    route on this router; an unregistered call is a failure, which is what keeps the
    suite honest about never touching a live API.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def adapter_registry() -> Iterator[None]:
    """Restore the adapter registry after a test installs a stand-in."""
    registry.names()  # force the built-in adapters in before snapshotting
    snapshot = dict(registry._ADAPTERS)
    yield
    registry._ADAPTERS.clear()
    registry._ADAPTERS.update(snapshot)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def build_places_adapter(
    redis_client: Any, clock: FakeClock, *, meter: Any = None
) -> GooglePlacesAdapter:
    """The real adapter, wired to fakeredis and a clock that never really sleeps.

    Leaving `meter` unset keeps the production hook, which writes `api_calls` rows.
    """
    from app.modules.adapters.google_places.client import build_client

    return GooglePlacesAdapter(
        client_factory=lambda job_run_id: build_client(
            GooglePlacesAdapter.name,
            job_run_id=job_run_id,
            sleeper=clock.sleep,
            clock=clock,
            redis_client=redis_client,
            meter=meter,
        )
    )


@pytest.fixture
def places_adapter(fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock) -> GooglePlacesAdapter:
    """Install a Places adapter whose waiting is simulated, in place of the real one."""
    adapter = build_places_adapter(fake_redis, clock)
    registry.register(adapter, replace=True)
    return adapter


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
