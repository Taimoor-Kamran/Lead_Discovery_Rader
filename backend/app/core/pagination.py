"""Opaque cursor pagination for list endpoints.

The cursor encodes `(created_at, id)` of the last row of the previous page, which keeps
paging stable when rows are inserted while a client is paging.
"""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import Select, literal, tuple_
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import ValidationFailedError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    id: uuid.UUID


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.astimezone(UTC).isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> Cursor:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        created_raw, _, id_raw = base64.urlsafe_b64decode(padded).decode().partition("|")
        return Cursor(created_at=datetime.fromisoformat(created_raw), id=uuid.UUID(id_raw))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailedError("Cursor is not valid", details={"cursor": cursor}) from exc


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def apply_cursor[T](
    stmt: Select[tuple[T]],
    created_at_col: InstrumentedAttribute[datetime],
    id_col: InstrumentedAttribute[uuid.UUID],
    cursor: str | None,
) -> Select[tuple[T]]:
    """Restrict a descending `(created_at, id)` query to the rows after `cursor`."""
    if not cursor:
        return stmt
    position = decode_cursor(cursor)
    return stmt.where(
        tuple_(created_at_col, id_col) < tuple_(literal(position.created_at), literal(position.id))
    )
