"""Password hashing and JWT issue/verify. No secret value is ever logged or returned."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import MIN_JWT_SECRET_BYTES, Settings, get_settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger

logger = get_logger("app.security")

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


# A token minted before `token_version` existed is read as version 1, which is what
# every user starts at — so adding the claim did not log anybody out.
DEFAULT_TOKEN_VERSION = 1

WEAK_JWT_SECRET_MESSAGE = (
    "JWT_SECRET is only {actual} bytes long. HS256 needs at least "
    f"{MIN_JWT_SECRET_BYTES}"
    " bytes (RFC 7518 §3.2). Generate one with: openssl rand -hex 32"
)


class InsecureConfigurationError(RuntimeError):
    """Raised at startup when a setting is too weak for the environment it runs in.

    It stops the process on purpose: an API that signs tokens with a guessable secret is
    worse than an API that does not start.
    """


def check_jwt_secret(settings: Settings | None = None) -> None:
    """Refuse to start on a weak JWT secret outside development; warn inside it.

    Called from every entrypoint (the API factory, the worker, the CLI) so there is no
    way into the system that skips the check. The secret itself is never in the message.
    """
    config = settings or get_settings()
    if config.jwt_secret_is_strong:
        return
    actual = len(config.jwt_secret.get_secret_value().encode())
    message = WEAK_JWT_SECRET_MESSAGE.format(actual=actual)
    if config.requires_strong_jwt_secret:
        raise InsecureConfigurationError(
            f"{message} Refusing to start in environment '{config.environment}'."
        )
    logger.warning(
        "the JWT secret is shorter than the recommended minimum",
        extra={"jwt_secret_bytes": actual, "minimum_bytes": MIN_JWT_SECRET_BYTES},
    )


@dataclass(frozen=True)
class TokenClaims:
    subject: uuid.UUID
    role: str
    token_type: TokenType
    jti: str
    expires_at: datetime
    token_version: int = DEFAULT_TOKEN_VERSION


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _create_token(
    subject: uuid.UUID,
    role: str,
    token_type: TokenType,
    ttl: timedelta,
    token_version: int = DEFAULT_TOKEN_VERSION,
) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + ttl
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "tv": token_version,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires_at


def create_access_token(
    subject: uuid.UUID, role: str, token_version: int = DEFAULT_TOKEN_VERSION
) -> tuple[str, datetime]:
    settings = get_settings()
    return _create_token(
        subject,
        role,
        "access",
        timedelta(minutes=settings.access_token_ttl_minutes),
        token_version,
    )


def create_refresh_token(
    subject: uuid.UUID, role: str, token_version: int = DEFAULT_TOKEN_VERSION
) -> tuple[str, datetime]:
    settings = get_settings()
    return _create_token(
        subject,
        role,
        "refresh",
        timedelta(days=settings.refresh_token_ttl_days),
        token_version,
    )


def decode_token(token: str, expected_type: TokenType) -> TokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Token is invalid", code="token_invalid") from exc

    if payload.get("type") != expected_type:
        raise AuthenticationError("Token is of the wrong type", code="token_invalid")
    try:
        subject = uuid.UUID(str(payload["sub"]))
    except ValueError as exc:
        raise AuthenticationError("Token subject is invalid", code="token_invalid") from exc

    return TokenClaims(
        subject=subject,
        role=str(payload.get("role", "")),
        token_type=expected_type,
        jti=str(payload.get("jti", "")),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        token_version=int(payload.get("tv", DEFAULT_TOKEN_VERSION)),
    )
