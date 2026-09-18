"""User lifecycle and authentication. Every auth event writes an audit row."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.core.security import hash_password, needs_rehash, verify_password
from app.modules.audit import service as audit
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import UserCreate, UserRead, UserUpdate

logger = get_logger("app.auth")

# Returned for a failed login whatever the cause, so the endpoint cannot be used to
# find out which email addresses exist.
INVALID_CREDENTIALS = "Email or password is incorrect"


def get_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", details={"user_id": str(user_id)})
    return user


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalars(select(User).where(User.email == email)).first()


def create_user(
    session: Session, payload: UserCreate, *, actor_id: uuid.UUID | None = None
) -> User:
    user = User(
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            "A user with that email already exists", details={"email": str(payload.email)}
        ) from exc

    audit.record(
        session,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        actor_id=actor_id,
        after={"email": user.email, "role": user.role.value, "is_active": user.is_active},
    )
    return user


def update_user(
    session: Session,
    user_id: uuid.UUID,
    payload: UserUpdate,
    *,
    actor_id: uuid.UUID | None = None,
) -> User:
    user = get_user(session, user_id)
    before = {"role": user.role.value, "is_active": user.is_active}

    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    session.flush()

    audit.record(
        session,
        action="user.updated",
        entity_type="user",
        entity_id=user.id,
        actor_id=actor_id,
        before=before,
        after={
            "role": user.role.value,
            "is_active": user.is_active,
            "password_changed": payload.password is not None,
        },
    )
    return user


def list_users(
    session: Session, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> Page[UserRead]:
    stmt = select(User).order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)
    stmt = apply_cursor(stmt, User.created_at, User.id, cursor)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[UserRead](
        items=[UserRead.model_validate(row) for row in rows], next_cursor=next_cursor
    )


def authenticate(session: Session, email: str, password: str) -> User:
    """Verify credentials, or raise AuthenticationError. Both outcomes are audited."""
    user = get_user_by_email(session, email)

    if user is None or not verify_password(password, user.password_hash):
        audit.record(
            session,
            action="auth.login_failed",
            entity_type="user",
            entity_id=str(user.id) if user else email,
            actor_id=user.id if user else None,
            after={"reason": "bad_credentials"},
        )
        session.commit()
        logger.warning("login failed", extra={"reason": "bad_credentials"})
        raise AuthenticationError(INVALID_CREDENTIALS, code="invalid_credentials")

    if not user.is_active:
        audit.record(
            session,
            action="auth.login_failed",
            entity_type="user",
            entity_id=user.id,
            actor_id=user.id,
            after={"reason": "inactive"},
        )
        session.commit()
        logger.warning("login failed", extra={"reason": "inactive"})
        raise AuthenticationError(INVALID_CREDENTIALS, code="invalid_credentials")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        session.flush()

    audit.record(
        session,
        action="auth.login_succeeded",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        after={"role": user.role.value},
    )
    logger.info("login succeeded", extra={"user_id": str(user.id), "role": user.role.value})
    return user


def record_logout(session: Session, user_id: uuid.UUID | None) -> None:
    audit.record(
        session,
        action="auth.logout",
        entity_type="user",
        entity_id=str(user_id) if user_id else "anonymous",
        actor_id=user_id,
    )


def record_refresh(session: Session, user: User) -> None:
    audit.record(
        session,
        action="auth.token_refreshed",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        after={"role": user.role.value},
    )


def ensure_admin(session: Session, email: str, password: str) -> tuple[User, bool]:
    """Create the bootstrap admin, or promote an existing user. Used by `make seed-admin`."""
    existing = get_user_by_email(session, email)
    if existing is not None:
        changed = existing.role is not Role.admin or not existing.is_active
        if changed:
            existing.role = Role.admin
            existing.is_active = True
            session.flush()
            audit.record(
                session,
                action="user.updated",
                entity_type="user",
                entity_id=existing.id,
                after={"role": Role.admin.value, "is_active": True, "reason": "seed-admin"},
            )
        return existing, False
    user = create_user(
        session, UserCreate(email=email, password=password, role=Role.admin, is_active=True)
    )
    return user, True
