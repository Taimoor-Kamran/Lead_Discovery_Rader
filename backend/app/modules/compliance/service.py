"""Suppressions: who must never be proposed again, and how the rest of the system asks.

`is_suppressed` is the one question everything else asks — classification before it
creates a pending opportunity, the review queue and the leads list before they show a
business. It matches on the business id, its domain and its phone, so a business that
comes back under a new id after a re-discovery is still caught.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Select, exists, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.modules.audit import service as audit
from app.modules.businesses.models import Business
from app.modules.compliance.models import Suppression, SuppressionSource
from app.modules.compliance.schemas import SuppressionCreate, SuppressionRead
from app.modules.normalization.phones import to_e164
from app.modules.normalization.web import parse_website

logger = get_logger("app.compliance")


# --- asking ------------------------------------------------------------------------------


def active_suppressions_for(session: Session, business: Business) -> list[Suppression]:
    """Every active suppression that covers this business, by id, domain or phone."""
    conditions = [Suppression.business_id == business.id]
    if business.domain:
        conditions.append(Suppression.domain == business.domain)
    if business.phone_e164:
        conditions.append(Suppression.phone_e164 == business.phone_e164)
    return list(
        session.scalars(
            select(Suppression).where(Suppression.lifted_at.is_(None), or_(*conditions))
        )
    )


def is_suppressed(session: Session, business: Business) -> bool:
    return bool(active_suppressions_for(session, business))


def suppressed_condition() -> Select[tuple[uuid.UUID]]:
    """The active suppressions covering the `Business` row of the enclosing query."""
    return select(Suppression.id).where(
        Suppression.lifted_at.is_(None),
        or_(
            Suppression.business_id == Business.id,
            (Suppression.domain.is_not(None)) & (Suppression.domain == Business.domain),
            (Suppression.phone_e164.is_not(None)) & (Suppression.phone_e164 == Business.phone_e164),
        ),
    )


def not_suppressed() -> ColumnElement[bool]:
    """`NOT EXISTS (active suppression matching Business)` for a query that selects Business."""
    return ~exists(suppressed_condition())


# --- adding and lifting ---------------------------------------------------------------


def suppress_business(
    session: Session,
    business: Business,
    *,
    reason: str,
    source: SuppressionSource,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> Suppression:
    """Add an active suppression covering the business, its domain and its phone."""
    row = Suppression(
        business_id=business.id,
        domain=business.domain,
        phone_e164=business.phone_e164,
        reason=reason,
        source=source,
        created_by=actor_id,
        created_at=now or datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        action="suppression.added",
        entity_type="suppression",
        entity_id=row.id,
        actor_id=actor_id,
        after=_snapshot(row),
    )
    logger.info(
        "business suppressed",
        extra={"business_id": str(business.id), "source": source.value},
    )
    _notify_crm(session, business.id, actor_id=actor_id, now=now)
    return row


def add_suppression(
    session: Session, payload: SuppressionCreate, *, actor_id: uuid.UUID
) -> Suppression:
    """An admin adds a suppression by business, by domain and/or by phone."""
    business: Business | None = None
    if payload.business_id is not None:
        business = session.get(Business, payload.business_id)
        if business is None:
            raise NotFoundError(
                "Business not found", details={"business_id": str(payload.business_id)}
            )
    domain = _clean_domain(payload.domain)
    phone = _clean_phone(payload.phone_e164)
    if business is None and domain is None and phone is None:
        raise ValidationFailedError(
            "A suppression needs a business, a domain or a phone number",
            details={"fields": ["business_id", "domain", "phone_e164"]},
        )
    row = Suppression(
        business_id=business.id if business is not None else None,
        domain=domain if domain is not None else (business.domain if business else None),
        phone_e164=phone if phone is not None else (business.phone_e164 if business else None),
        reason=payload.reason.strip(),
        source=SuppressionSource.admin,
        created_by=actor_id,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        action="suppression.added",
        entity_type="suppression",
        entity_id=row.id,
        actor_id=actor_id,
        after=_snapshot(row),
    )
    _notify_crm(session, row.business_id, actor_id=actor_id)
    return row


def lift_suppression(
    session: Session, suppression_id: uuid.UUID, *, actor_id: uuid.UUID
) -> Suppression:
    row = session.get(Suppression, suppression_id)
    if row is None:
        raise NotFoundError(
            "Suppression not found", details={"suppression_id": str(suppression_id)}
        )
    if row.lifted_at is not None:
        raise ConflictError(
            "This suppression was already lifted",
            details={"suppression_id": str(row.id), "lifted_at": row.lifted_at.isoformat()},
        )
    _lift(session, row, actor_id=actor_id)
    return row


def _lift(session: Session, row: Suppression, *, actor_id: uuid.UUID) -> None:
    before = _snapshot(row)
    row.lifted_at = datetime.now(UTC)
    row.lifted_by = actor_id
    session.flush()
    audit.record(
        session,
        action="suppression.lifted",
        entity_type="suppression",
        entity_id=row.id,
        actor_id=actor_id,
        before=before,
        after=_snapshot(row),
    )
    _notify_crm(session, row.business_id, actor_id=actor_id)


def _notify_crm(
    session: Session,
    business_id: uuid.UUID | None,
    *,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """A do-not-contact must reach a record already in the CRM; a lift must clear it."""
    from app.modules.crm import service as crm

    crm.on_suppression_changed(session, business_id, actor_id=actor_id, now=now)


def lift_review_suppressions(
    session: Session, business_id: uuid.UUID, *, actor_id: uuid.UUID
) -> int:
    """Undoing a do-not-contact lifts the review suppression(s) it created. Admin rows stay."""
    rows = list(
        session.scalars(
            select(Suppression).where(
                Suppression.business_id == business_id,
                Suppression.source == SuppressionSource.review,
                Suppression.lifted_at.is_(None),
            )
        )
    )
    for row in rows:
        _lift(session, row, actor_id=actor_id)
    return len(rows)


# --- reading ------------------------------------------------------------------------------


def get_suppression(session: Session, suppression_id: uuid.UUID) -> Suppression:
    row = session.get(Suppression, suppression_id)
    if row is None:
        raise NotFoundError(
            "Suppression not found", details={"suppression_id": str(suppression_id)}
        )
    return row


def list_suppressions(
    session: Session,
    *,
    active_only: bool = True,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[SuppressionRead]:
    stmt = (
        select(Suppression)
        .order_by(Suppression.created_at.desc(), Suppression.id.desc())
        .limit(limit + 1)
    )
    if active_only:
        stmt = stmt.where(Suppression.lifted_at.is_(None))
    stmt = apply_cursor(stmt, Suppression.created_at, Suppression.id, cursor)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    names = _business_names(session, [row.business_id for row in rows if row.business_id])
    return Page[SuppressionRead](
        items=[read(row, names.get(row.business_id) if row.business_id else None) for row in rows],
        next_cursor=next_cursor,
    )


def read(row: Suppression, business_name: str | None = None) -> SuppressionRead:
    return SuppressionRead(
        id=row.id,
        business_id=row.business_id,
        business_name=business_name,
        domain=row.domain,
        phone_e164=row.phone_e164,
        reason=row.reason,
        source=row.source,
        created_by=row.created_by,
        created_at=row.created_at,
        lifted_at=row.lifted_at,
        lifted_by=row.lifted_by,
        active=row.lifted_at is None,
    )


def _business_names(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = session.execute(
        select(Business.id, Business.display_name).where(Business.id.in_(set(ids)))
    ).all()
    return {row.id: row.display_name for row in rows}


def _snapshot(row: Suppression) -> dict[str, object]:
    return {
        "business_id": str(row.business_id) if row.business_id else None,
        "domain": row.domain,
        "phone_e164": row.phone_e164,
        "reason": row.reason,
        "source": row.source.value,
        "lifted_at": row.lifted_at.isoformat() if row.lifted_at else None,
    }


def _clean_domain(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    _, domain, _ = parse_website(value.strip())
    if not domain:
        raise ValidationFailedError("Domain is not valid", details={"domain": value})
    return domain


def _clean_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    phone = to_e164(value.strip())
    if not phone:
        raise ValidationFailedError("Phone number is not valid", details={"phone_e164": value})
    return phone
