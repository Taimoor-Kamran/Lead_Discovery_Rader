"""Reading and enabling/disabling sources, and validating the sources a job asks for."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.audit import service as audit
from app.modules.sources.models import Source
from app.modules.sources.schemas import SourceRead

logger = get_logger("app.sources")


def list_sources(session: Session) -> list[SourceRead]:
    """Every registered source, in name order. The set is small; no pagination needed."""
    rows = session.scalars(select(Source).order_by(Source.name))
    return [SourceRead.model_validate(row) for row in rows]


def get_source(session: Session, source_id: uuid.UUID) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise NotFoundError("Source not found", details={"source_id": str(source_id)})
    return source


def get_source_by_name(session: Session, name: str) -> Source | None:
    return session.scalars(select(Source).where(Source.name == name)).first()


def set_enabled(
    session: Session, source_id: uuid.UUID, *, enabled: bool, actor_id: uuid.UUID
) -> Source:
    source = get_source(session, source_id)
    before = source.enabled
    source.enabled = enabled
    session.flush()
    audit.record(
        session,
        action="source.enabled" if enabled else "source.disabled",
        entity_type="source",
        entity_id=source.id,
        actor_id=actor_id,
        before={"enabled": before},
        after={"enabled": enabled},
    )
    logger.info("source availability changed", extra={"source": source.name, "enabled": enabled})
    return source


def validate_source_ids(session: Session, source_ids: list[uuid.UUID]) -> list[Source]:
    """Reject a job that names a source which does not exist or is switched off."""
    if not source_ids:
        return []
    wanted = list(dict.fromkeys(source_ids))
    found = {row.id: row for row in session.scalars(select(Source).where(Source.id.in_(wanted)))}

    unknown = [str(s) for s in wanted if s not in found]
    if unknown:
        raise ValidationFailedError(
            "One or more sources do not exist", details={"unknown_source_ids": unknown}
        )
    disabled = [found[s].name for s in wanted if not found[s].enabled]
    if disabled:
        raise ValidationFailedError(
            "One or more sources are disabled", details={"disabled_sources": disabled}
        )
    return [found[s] for s in wanted]
