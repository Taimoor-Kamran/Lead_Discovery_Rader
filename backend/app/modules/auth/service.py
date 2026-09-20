"""User lifecycle and authentication. Every auth event writes an audit row.

Login protection (spec v0.8.0 §5), in the order `authenticate` applies it:

1. more than `LOGIN_MAX_FAILURES` failures for the same email + client address inside
   `LOGIN_WINDOW_MINUTES` → 429 before the password is even looked at (Redis counter);
2. an account locked by `LOGIN_LOCKOUT_FAILURES` failures answers the generic 401 for
   `LOGIN_LOCKOUT_MINUTES`, so the response never says whether the account exists;
3. a correct password resets both counters and stamps `last_login_at`.

Every failure, lock and rate-limit is an audit row.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    TooManyRequestsError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.core.redis import get_redis
from app.core.security import hash_password, needs_rehash, verify_password
from app.modules.audit import service as audit
from app.modules.auth.models import Role, User
from app.modules.auth.passwords import validate_new_password
from app.modules.auth.schemas import MIN_PASSWORD_LENGTH, UserCreate, UserRead, UserUpdate

logger = get_logger("app.auth")

# Returned for a failed login whatever the cause, so the endpoint cannot be used to
# find out which email addresses exist.
INVALID_CREDENTIALS = "Email or password is incorrect"
TOO_MANY_ATTEMPTS = "Too many failed sign-in attempts. Try again in {minutes} minutes."
LOGIN_FAILURE_KEY = "login:failures"
UNKNOWN_CLIENT = "unknown"


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
    validate_new_password(payload.password, email=str(payload.email))
    user = User(
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
        must_change_password=payload.must_change_password,
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
        after={
            "email": user.email,
            "role": user.role.value,
            "is_active": user.is_active,
            "must_change_password": user.must_change_password,
        },
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
        # A password set by an admin is temporary: the user replaces it on next sign-in,
        # and every token issued under the old one is retired.
        validate_new_password(payload.password, email=user.email)
        user.password_hash = hash_password(payload.password)
        user.must_change_password = True
        user.token_version += 1
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
    session: Session,
    *,
    role: Role | None = None,
    is_active: bool | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[UserRead]:
    stmt = select(User).order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    stmt = apply_cursor(stmt, User.created_at, User.id, cursor)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[UserRead](
        items=[UserRead.model_validate(row) for row in rows], next_cursor=next_cursor
    )


def _failure_key(email: str, client_ip: str) -> str:
    return f"{LOGIN_FAILURE_KEY}:{client_ip or UNKNOWN_CLIENT}:{email.strip().lower()}"


def failures_in_window(email: str, client_ip: str) -> int:
    raw = get_redis().get(_failure_key(email, client_ip))
    return int(raw) if isinstance(raw, bytes | str) else 0


def _count_failure(email: str, client_ip: str) -> int:
    settings = get_settings()
    key = _failure_key(email, client_ip)
    redis = get_redis()
    count = int(cast(int, redis.incr(key)))
    if count == 1:
        redis.expire(key, settings.login_window_minutes * 60)
    return count


def _clear_failures(email: str, client_ip: str) -> None:
    get_redis().delete(_failure_key(email, client_ip))


def is_locked(user: User, *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(UTC)
    return user.locked_until is not None and user.locked_until > moment


def _fail(
    session: Session,
    *,
    email: str,
    client_ip: str,
    user: User | None,
    reason: str,
    now: datetime,
) -> AuthenticationError:
    """Record one failed attempt everywhere it counts and build the generic 401."""
    settings = get_settings()
    _count_failure(email, client_ip)
    after: dict[str, object] = {"reason": reason, "client": client_ip or UNKNOWN_CLIENT}
    if user is not None and reason == "bad_credentials":
        user.failed_login_count += 1
        after["failed_login_count"] = user.failed_login_count
        if user.failed_login_count >= settings.login_lockout_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
            session.flush()
            audit.record(
                session,
                action="auth.account_locked",
                entity_type="user",
                entity_id=user.id,
                actor_id=None,
                after={
                    "locked_until": user.locked_until.isoformat(),
                    "failures": settings.login_lockout_failures,
                    "client": client_ip or UNKNOWN_CLIENT,
                },
            )
            logger.warning(
                "account locked after repeated failures", extra={"user_id": str(user.id)}
            )
        session.flush()
    audit.record(
        session,
        action="auth.login_failed",
        entity_type="user",
        entity_id=str(user.id) if user else email,
        actor_id=user.id if user else None,
        after=after,
    )
    session.commit()
    logger.warning("login failed", extra={"reason": reason})
    return AuthenticationError(INVALID_CREDENTIALS, code="invalid_credentials")


def authenticate(
    session: Session,
    email: str,
    password: str,
    *,
    client_ip: str = UNKNOWN_CLIENT,
    now: datetime | None = None,
) -> User:
    """Verify credentials, or raise. Every outcome is audited; see the module docstring."""
    settings = get_settings()
    moment = now or datetime.now(UTC)

    if failures_in_window(email, client_ip) >= settings.login_max_failures:
        audit.record(
            session,
            action="auth.login_rate_limited",
            entity_type="user",
            entity_id=email,
            after={
                "client": client_ip or UNKNOWN_CLIENT,
                "window_minutes": settings.login_window_minutes,
            },
        )
        session.commit()
        logger.warning("login rate limited", extra={"client": client_ip or UNKNOWN_CLIENT})
        raise TooManyRequestsError(
            TOO_MANY_ATTEMPTS.format(minutes=settings.login_window_minutes),
            details={"retry_after_minutes": settings.login_window_minutes},
        )

    user = get_user_by_email(session, email)
    if user is None:
        raise _fail(
            session,
            email=email,
            client_ip=client_ip,
            user=None,
            reason="bad_credentials",
            now=moment,
        )
    if is_locked(user, now=moment):
        raise _fail(
            session, email=email, client_ip=client_ip, user=user, reason="locked", now=moment
        )
    if not verify_password(password, user.password_hash):
        raise _fail(
            session,
            email=email,
            client_ip=client_ip,
            user=user,
            reason="bad_credentials",
            now=moment,
        )
    if not user.is_active:
        raise _fail(
            session, email=email, client_ip=client_ip, user=user, reason="inactive", now=moment
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = moment
    session.flush()
    _clear_failures(email, client_ip)

    audit.record(
        session,
        action="auth.login_succeeded",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        after={"role": user.role.value, "client": client_ip or UNKNOWN_CLIENT},
    )
    logger.info("login succeeded", extra={"user_id": str(user.id), "role": user.role.value})
    return user


def change_password(
    session: Session, user: User, *, current_password: str, new_password: str
) -> User:
    """The user replaces their own password. Retires every other session they hold."""
    if not verify_password(current_password, user.password_hash):
        audit.record(
            session,
            action="auth.password_change_failed",
            entity_type="user",
            entity_id=user.id,
            actor_id=user.id,
            after={"reason": "bad_current_password"},
        )
        session.commit()
        raise AuthenticationError("The current password is incorrect", code="invalid_credentials")
    validate_new_password(new_password, email=user.email)
    if verify_password(new_password, user.password_hash):
        raise ValidationFailedError(
            "The new password must be different from the current one",
            details={"rules": ["it must be different from the current password"]},
            code="weak_password",
        )
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1
    session.flush()
    audit.record(
        session,
        action="user.password_changed",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        after={"token_version": user.token_version, "must_change_password": False},
    )
    logger.info("password changed", extra={"user_id": str(user.id)})
    return user


def admin_reset_password(
    session: Session, user_id: uuid.UUID, new_password: str, *, actor_id: uuid.UUID
) -> User:
    """An admin sets a temporary password; the user must change it on next sign-in."""
    user = get_user(session, user_id)
    validate_new_password(new_password, email=user.email)
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.token_version += 1
    user.failed_login_count = 0
    user.locked_until = None
    session.flush()
    audit.record(
        session,
        action="user.password_reset",
        entity_type="user",
        entity_id=user.id,
        actor_id=actor_id,
        after={"via": "admin", "token_version": user.token_version, "must_change_password": True},
    )
    logger.info("password reset by an admin", extra={"user_id": str(user.id)})
    return user


def unlock_user(session: Session, user_id: uuid.UUID, *, actor_id: uuid.UUID) -> User:
    """Lift a lockout early and forget the failures that caused it."""
    user = get_user(session, user_id)
    before = {
        "locked_until": user.locked_until.isoformat() if user.locked_until else None,
        "failed_login_count": user.failed_login_count,
    }
    user.locked_until = None
    user.failed_login_count = 0
    session.flush()
    audit.record(
        session,
        action="user.unlocked",
        entity_type="user",
        entity_id=user.id,
        actor_id=actor_id,
        before=before,
        after={"locked_until": None, "failed_login_count": 0},
    )
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


def reset_password(session: Session, email: str, new_password: str) -> User:
    """Set a new password and retire every token the user already holds.

    Raises `NotFoundError` for an unknown email and `ValidationFailedError` for a
    password that is too short — both before anything is written.
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailedError(
            f"A password must be at least {MIN_PASSWORD_LENGTH} characters",
            details={"minimum_length": MIN_PASSWORD_LENGTH},
        )

    user = get_user_by_email(session, email)
    if user is None:
        raise NotFoundError("No user has that email address", details={"email": email})

    validate_new_password(new_password, email=user.email)
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.token_version += 1
    user.failed_login_count = 0
    user.locked_until = None
    session.flush()

    audit.record(
        session,
        action="user.password_reset",
        entity_type="user",
        entity_id=user.id,
        # A reset is run from a terminal, so there is no authenticated actor to record.
        actor_id=None,
        after={"via": "cli", "token_version": user.token_version, "must_change_password": True},
    )
    logger.info("password reset", extra={"user_id": str(user.id), "via": "cli"})
    return user


def ensure_user(session: Session, email: str, password: str, role: Role) -> tuple[User, bool]:
    """Create a user with this role, or give an existing one the role. Used by demo seeding.

    An existing user keeps its password: seeding is repeatable and never resets anyone.
    """
    existing = get_user_by_email(session, email)
    if existing is not None:
        changed = existing.role is not role or not existing.is_active
        if changed:
            before = {"role": existing.role.value, "is_active": existing.is_active}
            existing.role = role
            existing.is_active = True
            session.flush()
            audit.record(
                session,
                action="user.updated",
                entity_type="user",
                entity_id=existing.id,
                before=before,
                after={"role": role.value, "is_active": True, "reason": "seed-demo-users"},
            )
        return existing, False
    user = create_user(
        session,
        UserCreate(
            email=email, password=password, role=role, is_active=True, must_change_password=False
        ),
    )
    return user, True


def ensure_admin(
    session: Session, email: str, password: str, *, must_change_password: bool = False
) -> tuple[User, bool]:
    """Create the bootstrap admin, or promote an existing user. Used by `make seed-admin`.

    `must_change_password=True` is what a generated (printed-once) password gets.
    """
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
        session,
        UserCreate(
            email=email,
            password=password,
            role=Role.admin,
            is_active=True,
            must_change_password=must_change_password,
        ),
    )
    return user, True
