"""Password hashing and JWT issue/verify. No secret value is ever logged or returned."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AuthenticationError

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


# A token minted before `token_version` existed is read as version 1, which is what
# every user starts at — so adding the claim did not log anybody out.
DEFAULT_TOKEN_VERSION = 1


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
