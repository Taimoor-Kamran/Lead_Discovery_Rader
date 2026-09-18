"""Structured JSON logging with request-ID correlation and secret redaction."""

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

from app.core.config import get_settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

REDACTED = "[REDACTED]"

# Keys whose values must never reach a log line, whatever the nesting.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "secret",
        "jwt_secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "cookie",
        "set-cookie",
    }
)

_KV_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(sorted(SENSITIVE_KEYS)) + r")\b\s*[=:]\s*(\"[^\"]*\"|'[^']*'|\S+)"
)
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}\b")


def _literal_secrets() -> list[str]:
    """Secret values pulled from settings, so their literal text can be scrubbed."""
    settings = get_settings()
    values = [
        settings.jwt_secret.get_secret_value(),
        settings.database_url,
        settings.redis_url,
    ]
    return [v for v in values if v and len(v) >= 6]


def scrub(text: str) -> str:
    """Remove secret literals and sensitive key/value pairs from a string."""
    for secret in _literal_secrets():
        if secret in text:
            text = text.replace(secret, REDACTED)
    text = _KV_PATTERN.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", text)
    return _JWT_PATTERN.sub(REDACTED, text)


def scrub_value(value: Any) -> Any:
    """Recursively scrub a structure destined for a log record."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in SENSITIVE_KEYS else scrub_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(item) for item in value)
    if isinstance(value, str):
        return scrub(value)
    return value


_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Renders a log record as a single JSON object, with every value scrubbed."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
            "request_id": getattr(record, "request_id", request_id_ctx.get()),
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        extras.pop("request_id", None)
        if extras:
            payload.update(scrub_value(extras))
        if record.exc_info:
            payload["exception"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get()
        return True


def configure_logging() -> None:
    """Install the JSON handler on the root logger. Safe to call more than once."""
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(noisy)
        logger.handlers = []
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
