"""Writing audit rows. The only supported operation is append."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


def record(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    actor_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit row. Flushed immediately so it shares the caller's transaction."""
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=before,
        after=after,
    )
    session.add(entry)
    session.flush()
    return entry
