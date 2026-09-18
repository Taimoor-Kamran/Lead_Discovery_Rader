"""A whole request cycle must not leave a secret in the log stream."""

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import JsonFormatter, RequestIdFilter
from app.modules.auth.models import User
from tests.conftest import TEST_JWT_SECRET, TEST_PASSWORD, auth_headers, geo_payload


class CapturingHandler(logging.Handler):
    """Captures the exact text the JSON handler would write to stdout."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())
        self.addFilter(RequestIdFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def captured_logs() -> Iterator[CapturingHandler]:
    handler = CapturingHandler()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous_level)


def test_no_secret_reaches_the_logs_during_a_full_session(
    client: TestClient, db: Session, sales_user: User, captured_logs: CapturingHandler
) -> None:
    headers = auth_headers(client, sales_user)
    token = headers["Authorization"].removeprefix("Bearer ")

    client.post("/api/v1/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD})
    client.post("/api/v1/auth/login", json={"email": sales_user.email, "password": "the-wrong-one"})
    client.get("/api/v1/auth/me", headers=headers)
    job_id = client.post(
        "/api/v1/search-jobs",
        json={"name": "logged", "geo": geo_payload(), "industry": "dental", "source_ids": []},
        headers=headers,
    ).json()["id"]
    client.post(f"/api/v1/search-jobs/{job_id}/run", headers=headers)
    client.post("/api/v1/auth/refresh")

    output = "\n".join(captured_logs.lines)
    assert output, "expected the request cycle to log something"

    settings = get_settings()
    assert TEST_JWT_SECRET not in output
    assert settings.jwt_secret.get_secret_value() not in output
    assert TEST_PASSWORD not in output
    assert "the-wrong-one" not in output
    assert token not in output
    assert sales_user.password_hash not in output
    assert settings.database_url not in output

    for line in captured_logs.lines:
        json.loads(line)  # every line is a single JSON object


def test_an_explicitly_logged_secret_is_redacted(captured_logs: CapturingHandler) -> None:
    logging.getLogger("app.test").info(
        "leaky", extra={"password": TEST_PASSWORD, "jwt_secret": TEST_JWT_SECRET}
    )
    output = "\n".join(captured_logs.lines)
    assert TEST_PASSWORD not in output
    assert TEST_JWT_SECRET not in output
    assert "[REDACTED]" in output
