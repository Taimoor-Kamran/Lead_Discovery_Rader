"""Password hashing and JWT behaviour."""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.core.security import (
    InsecureConfigurationError,
    check_jwt_secret,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

PASSWORD = "a-perfectly-fine-password"


def test_hash_is_not_the_password_and_verifies() -> None:
    digest = hash_password(PASSWORD)
    assert PASSWORD not in digest
    assert verify_password(PASSWORD, digest)
    assert not verify_password("something else", digest)


def test_verify_rejects_a_malformed_hash() -> None:
    assert not verify_password(PASSWORD, "not-a-hash")


def test_access_token_round_trip() -> None:
    subject = uuid.uuid4()
    token, expires_at = create_access_token(subject, "sales_rep")
    claims = decode_token(token, "access")
    assert claims.subject == subject
    assert claims.role == "sales_rep"
    assert claims.token_type == "access"
    assert expires_at > datetime.now(UTC)


def test_access_token_expires_after_the_configured_ttl() -> None:
    _, expires_at = create_access_token(uuid.uuid4(), "admin")
    ttl = expires_at - datetime.now(UTC)
    assert timedelta(minutes=14) < ttl <= timedelta(minutes=15)


def test_refresh_token_is_not_accepted_as_an_access_token() -> None:
    token, _ = create_refresh_token(uuid.uuid4(), "admin")
    with pytest.raises(AuthenticationError):
        decode_token(token, "access")


def test_expired_token_is_rejected() -> None:
    settings = get_settings()
    past = datetime.now(UTC) - timedelta(minutes=1)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "admin",
            "type": "access",
            "exp": int(past.timestamp()),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(AuthenticationError) as exc:
        decode_token(token, "access")
    assert exc.value.code == "token_expired"


def test_token_signed_with_another_secret_is_rejected() -> None:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "admin",
            "type": "access",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        # At least 32 bytes: PyJWT warns about a shorter HMAC key, and `make check`
        # must stay free of warnings that are really about the test, not the code.
        "a-different-secret-of-at-least-32-bytes",
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError) as exc:
        decode_token(token, "access")
    assert exc.value.code == "token_invalid"


def test_a_weak_jwt_secret_only_warns_in_development(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(environment="development", jwt_secret=SecretStr("too-short"))

    with caplog.at_level(logging.WARNING, logger="app.security"):
        check_jwt_secret(settings)

    assert "shorter than the recommended minimum" in caplog.text


def test_a_weak_jwt_secret_refuses_to_start_in_production() -> None:
    settings = Settings(environment="production", jwt_secret=SecretStr("0123456789abcdef"))

    with pytest.raises(InsecureConfigurationError) as exc:
        check_jwt_secret(settings)

    message = str(exc.value)
    assert "16 bytes" in message
    assert "openssl rand -hex 32" in message
    assert "0123456789abcdef" not in message, "the secret itself is never in the message"


def test_a_strong_jwt_secret_starts_anywhere() -> None:
    for environment in ("development", "staging", "production"):
        check_jwt_secret(
            Settings(environment=environment, jwt_secret=SecretStr(secrets.token_hex(32)))
        )
