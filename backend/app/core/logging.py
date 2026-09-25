"""Structured JSON logging with request-ID correlation and secret redaction."""

import json
import logging
import logging.handlers
import os
import re
import sys
from contextvars import ContextVar
from typing import Any

from pydantic import SecretStr

from app.core.config import get_settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

REDACTED = "[REDACTED]"

# Third-party HTTP loggers held at WARNING: at INFO they print every request URL.
QUIET_HTTP_LOGGERS = ("httpx", "httpcore")

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
        "x-goog-api-key",
        "google_places_api_key",
        "airtable_token",
        "cookie",
        "set-cookie",
    }
)

_KV_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(sorted(SENSITIVE_KEYS)) + r")\b\s*[=:]\s*(\"[^\"]*\"|'[^']*'|\S+)"
)
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}\b")


# A credential in a URL's query string — `?key=…`, `&access_token=…` — whatever it belongs
# to. PSI took its key this way, and httpx logged the whole URL (v0.11.0).
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)([?&](?:key|api_key|apikey|access_token|token|secret)=)[^&#\s\"']+"
)


def _literal_secrets() -> list[str]:
    """Every secret value in settings, so its literal text can be scrubbed.

    Collected from the `SecretStr` fields rather than listed by hand: a hand-kept list is
    how the PageSpeed key was left out and reached the logs. The connection URLs carry
    passwords but are plain strings, so they are named.
    """
    settings = get_settings()
    values = [
        value.get_secret_value()
        for name in type(settings).model_fields
        if isinstance(value := getattr(settings, name), SecretStr)
    ]
    values += [settings.database_url, settings.redis_url]
    return [v for v in values if v and len(v) >= 6]


def scrub(text: str) -> str:
    """Remove secret literals and sensitive key/value pairs from a string."""
    for secret in _literal_secrets():
        if secret in text:
            text = text.replace(secret, REDACTED)
    text = _URL_CREDENTIAL_PATTERN.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
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

# Where `log_fields()` parks its values. `extra={"created": ...}` would raise, because
# `created` is the LogRecord's own timestamp; nested under one key, nothing can collide.
FIELDS_KEY = "fields"


def log_fields(**fields: Any) -> dict[str, Any]:
    """Build an `extra` whose keys cannot clash with a LogRecord attribute.

    `logging` raises `KeyError` if `extra` carries a name a LogRecord already uses, and
    the names it uses (`created`, `module`, `name`, `process`, …) are ordinary words that
    turn up in result summaries. Pass anything computed through here:

        logger.info("resolution finished", extra=log_fields(**summary.model_dump()))
    """
    return {FIELDS_KEY: fields}


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
        nested = extras.pop(FIELDS_KEY, None)
        if isinstance(nested, dict):
            # Flattened back out, so a field parked by `log_fields()` reads the same as
            # one passed directly.
            extras.update(nested)
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


def file_handler(directory: str, filename: str, keep_days: int) -> logging.Handler:
    """A daily-rotating JSON log file: `<dir>/<file>`, `<file>.YYYY-MM-DD` for older days.

    Rotation happens at midnight of the process's local time; `keep_days` old files are
    kept. The formatter scrubs secrets exactly as the stdout handler does.
    """
    os.makedirs(directory, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(directory, filename),
        when="midnight",
        backupCount=max(int(keep_days), 1),
        encoding="utf-8",
        utc=True,
    )
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    return handler


def configure_logging() -> None:
    """Install the JSON handler on the root logger (and the rotating file when LOG_DIR is
    set). Safe to call more than once."""
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        if isinstance(existing, logging.FileHandler):
            existing.close()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    if settings.log_dir.strip():
        try:
            root.addHandler(
                file_handler(settings.log_dir.strip(), settings.log_file, settings.log_keep_days)
            )
        except OSError as exc:
            # A log file must never take the service down; stdout still has every line.
            root.warning(
                "log file is not writable; logging to stdout only",
                extra={"log_dir": settings.log_dir, "error": f"{type(exc).__name__}: {exc}"},
            )

    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(noisy)
        logger.handlers = []
        logger.propagate = True

    # httpx logs every request's full URL at INFO. A URL can carry a credential — the
    # PageSpeed key did, in plain text, until v0.11.0 — and our own client already logs
    # what matters about each call (`api_calls`, warnings), so only their warnings pass.
    for chatty in QUIET_HTTP_LOGGERS:
        logging.getLogger(chatty).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
