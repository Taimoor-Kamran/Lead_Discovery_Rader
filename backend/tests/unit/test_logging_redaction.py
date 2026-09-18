"""Structured logging must never emit a secret."""

import json
import logging
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.logging import REDACTED, JsonFormatter, scrub, scrub_value
from tests.conftest import TEST_JWT_SECRET, TEST_PASSWORD


def _format(record_kwargs: dict[str, Any], message: str = "event") -> dict[str, Any]:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    for key, value in record_kwargs.items():
        setattr(record, key, value)
    return dict(json.loads(JsonFormatter().format(record)))


def test_output_is_one_json_object_with_the_standard_fields() -> None:
    payload = _format({"user_id": "abc"}, message="hello")
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["user_id"] == "abc"
    assert "request_id" in payload


def test_the_jwt_secret_never_reaches_a_log_line() -> None:
    assert get_settings().jwt_secret.get_secret_value() == TEST_JWT_SECRET
    payload = _format({"detail": f"secret is {TEST_JWT_SECRET}"}, message=TEST_JWT_SECRET)
    serialized = json.dumps(payload)
    assert TEST_JWT_SECRET not in serialized
    assert REDACTED in serialized


@pytest.mark.parametrize(
    "message",
    [
        f"password={TEST_PASSWORD}",
        f'password: "{TEST_PASSWORD}"',
        f"Authorization: Bearer eyJhbGciOi.{TEST_PASSWORD}.sig",
    ],
)
def test_sensitive_key_value_pairs_are_scrubbed(message: str) -> None:
    assert TEST_PASSWORD not in scrub(message)


def test_nested_structures_are_scrubbed() -> None:
    cleaned = scrub_value(
        {"user": {"email": "a@b.test", "password": TEST_PASSWORD}, "tokens": ["eyJabcdefghijk"]}
    )
    assert cleaned == {
        "user": {"email": "a@b.test", "password": REDACTED},
        "tokens": [REDACTED],
    }


def test_the_database_url_is_scrubbed() -> None:
    url = get_settings().database_url
    assert url not in scrub(f"connecting to {url}")
