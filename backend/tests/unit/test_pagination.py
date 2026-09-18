"""Cursor encoding."""

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import ValidationFailedError
from app.core.pagination import decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    created_at = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)
    row_id = uuid.uuid4()
    decoded = decode_cursor(encode_cursor(created_at, row_id))
    assert decoded.created_at == created_at
    assert decoded.id == row_id


def test_cursor_is_opaque() -> None:
    row_id = uuid.uuid4()
    cursor = encode_cursor(datetime.now(UTC), row_id)
    assert str(row_id) not in cursor


@pytest.mark.parametrize("bad", ["", "!!!!", "bm90LWEtY3Vyc29y"])
def test_a_broken_cursor_is_a_validation_error(bad: str) -> None:
    with pytest.raises(ValidationFailedError):
        decode_cursor(bad)
