"""Structured logging must never emit a secret."""

import json
import logging
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.logging import REDACTED, JsonFormatter, log_fields, scrub, scrub_value
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


# --- v0.3.0: a field name that a LogRecord already uses -------------------------------


class _Capture(logging.Handler):
    """Keeps the records it is given, so a formatter can be run over them."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def emit(message: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Log one line through the real formatter and hand back the JSON it produced."""
    logger = logging.getLogger("test.log_fields")
    capture = _Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    try:
        logger.info(message, extra=log_fields(**fields))
    finally:
        logger.removeHandler(capture)
    payload: dict[str, Any] = json.loads(JsonFormatter().format(capture.records[0]))
    return payload


def test_log_fields_survives_a_name_the_log_record_already_owns() -> None:
    """`created` is a LogRecord's own timestamp, and also a resolution-run counter.

    Passing it straight through `extra` raises `KeyError`, which failed the run rather
    than the log line — a job dying because of how it reported itself.
    """
    payload = emit(
        "resolution finished",
        {"processed": 40, "created": 29, "linked_existing": 5, "invalid": 0},
    )

    assert payload["created"] == 29
    assert payload["processed"] == 40
    assert payload["message"] == "resolution finished"


def test_every_reserved_log_record_name_can_be_used_as_a_field() -> None:
    reserved = {"created": 1, "module": "x", "name": "y", "process": 2, "filename": "z"}

    payload = emit("done", reserved)

    for key, value in reserved.items():
        assert payload[key] == value


def test_a_secret_parked_in_log_fields_is_still_scrubbed() -> None:
    payload = emit("done", {"created": 1, "password": "hunter2-and-then-some"})

    assert payload["password"] == REDACTED
