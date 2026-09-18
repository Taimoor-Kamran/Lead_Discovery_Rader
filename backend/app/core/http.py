"""The outbound HTTP client every official-API adapter must use.

It is the single place where timeouts, retries, `Retry-After`, rate limiting, call
metering and secret redaction are implemented, so no adapter can quietly skip one.
Failure behaviour follows blueprint slide 44:

| Condition                     | Behaviour                                              |
|-------------------------------|--------------------------------------------------------|
| timeout / connection / 5xx    | exponential backoff + jitter, 3 attempts, TransientError |
| 429                           | honour `Retry-After` (capped), 3 attempts, RateLimitedError |
| 401 / 403                     | no retry, AuthError                                     |
| 400                           | no retry, AdapterError carrying the API's own message   |
| 2xx with an unparseable body  | one retry, then SchemaError                             |

This module makes no decision about *which* hosts may be called; v0.2.0 adapters only
call fixed, official API hosts. The SSRF guard for arbitrary business websites is v0.4.0.
"""

import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.logging import get_logger
from app.modules.adapters.errors import (
    AdapterError,
    AuthError,
    RateLimitedError,
    SchemaError,
    TransientError,
)

logger = get_logger("app.http")

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0
DEFAULT_TIMEOUT = httpx.Timeout(
    connect=CONNECT_TIMEOUT_SECONDS,
    read=READ_TIMEOUT_SECONDS,
    write=READ_TIMEOUT_SECONDS,
    pool=CONNECT_TIMEOUT_SECONDS,
)
DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 60.0
MAX_ERROR_BODY_CHARS = 4096
REDACTED = "[REDACTED]"


def _default_jitter() -> float:
    # Spreads retries from parallel workers; never used for anything security-sensitive.
    return random.random()  # noqa: S311


@dataclass(frozen=True)
class ApiCallRecord:
    """One outbound attempt, handed to the metering hook whether it worked or not."""

    source: str
    endpoint: str
    attempt: int
    duration_ms: int
    status_code: int | None = None
    error_class: str | None = None


class MeteringHook(Protocol):
    def __call__(self, record: ApiCallRecord) -> None: ...


class Limiter(Protocol):
    def acquire(self) -> None: ...


class ApiHttpClient:
    """A retrying JSON client for one source.

    `sleeper` and `jitter` are injectable so tests can assert a backoff schedule instead
    of waiting it out.
    """

    def __init__(
        self,
        *,
        source: str,
        meter: MeteringHook | None = None,
        limiter: Limiter | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = _default_jitter,
        secrets: Sequence[str] = (),
        client: httpx.Client | None = None,
    ) -> None:
        self.source = source
        self._meter = meter
        self._limiter = limiter
        self._max_attempts = max(max_attempts, 1)
        self._sleeper = sleeper
        self._jitter = jitter
        self._secrets = [s for s in secrets if s]
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ApiHttpClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def redact(self, text: str) -> str:
        """Remove every known secret literal from a string bound for a log or an error."""
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def request_json[T](
        self,
        method: str,
        url: str,
        *,
        parse: Callable[[Any], T],
        headers: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> T:
        """Send a request and parse its body, applying the whole failure policy above."""
        endpoint = str(httpx.URL(url).copy_with(query=None, fragment=None))
        attempt = 0
        transient_failures = 0
        rate_limited = 0
        schema_failures = 0

        while True:
            attempt += 1
            if self._limiter is not None:
                # Raises QuotaExceededError before anything leaves this process.
                self._limiter.acquire()

            started = time.perf_counter()
            try:
                response = self._client.request(method, url, headers=dict(headers or {}), json=json)
            except httpx.HTTPError as exc:
                self._record(endpoint, attempt, started, error_class=type(exc).__name__)
                transient_failures += 1
                if transient_failures >= self._max_attempts:
                    raise TransientError(
                        f"{self.source} did not answer after {transient_failures} attempts: "
                        f"{self.redact(type(exc).__name__)}",
                        details={"attempts": transient_failures},
                        source=self.source,
                    ) from exc
                self._sleeper(self._backoff(transient_failures))
                continue

            status = response.status_code
            self._record(endpoint, attempt, started, status_code=status)

            if response.is_success:
                try:
                    return parse(response.json())
                except Exception as exc:
                    schema_failures += 1
                    if schema_failures > 1:
                        raise SchemaError(
                            f"{self.source} answered {status} with a body this build cannot "
                            f"parse: {self.redact(type(exc).__name__)}",
                            details={"body": self._error_body(response), "status_code": status},
                            source=self.source,
                        ) from exc
                    logger.warning(
                        "unparseable response body, retrying once",
                        extra={"source": self.source, "endpoint": endpoint},
                    )
                    continue

            if status == httpx.codes.TOO_MANY_REQUESTS:
                rate_limited += 1
                if rate_limited >= self._max_attempts:
                    raise RateLimitedError(
                        f"{self.source} is rate limiting this client and did not recover after "
                        f"{rate_limited} attempts",
                        details={"attempts": rate_limited},
                        source=self.source,
                    )
                self._sleeper(self._retry_after(response, rate_limited))
                continue

            if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
                raise AuthError(
                    f"{self.source} rejected this request with {status}. Check "
                    "GOOGLE_PLACES_API_KEY, that the API is enabled for the project, and that "
                    "billing is on.",
                    details={"status_code": status, "body": self._error_body(response)},
                    source=self.source,
                )

            if status == httpx.codes.BAD_REQUEST:
                raise AdapterError(
                    f"{self.source} rejected this request as invalid: "
                    f"{self._api_message(response)}",
                    details={"status_code": status, "body": self._error_body(response)},
                    source=self.source,
                )

            if status >= httpx.codes.INTERNAL_SERVER_ERROR:
                transient_failures += 1
                if transient_failures >= self._max_attempts:
                    raise TransientError(
                        f"{self.source} answered {status} on {transient_failures} attempts",
                        details={"status_code": status, "body": self._error_body(response)},
                        source=self.source,
                    )
                self._sleeper(self._backoff(transient_failures))
                continue

            raise AdapterError(
                f"{self.source} answered an unexpected {status}",
                details={"status_code": status, "body": self._error_body(response)},
                source=self.source,
            )

    def _backoff(self, failures: int) -> float:
        return float(BACKOFF_BASE_SECONDS * (2 ** (failures - 1)) * (1.0 + self._jitter()))

    def _retry_after(self, response: httpx.Response, failures: int) -> float:
        """Honour `Retry-After` when the header is a sane number of seconds."""
        raw = response.headers.get("Retry-After", "").strip()
        try:
            seconds = float(raw)
        except ValueError:
            return self._backoff(failures)
        return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)

    def _error_body(self, response: httpx.Response) -> str:
        try:
            text = response.text
        except Exception:  # pragma: no cover - a body that cannot even be decoded
            return ""
        return self.redact(text)[:MAX_ERROR_BODY_CHARS]

    def _api_message(self, response: httpx.Response) -> str:
        """Pull the provider's own message out of a Google-style error envelope."""
        try:
            body = response.json()
        except Exception:
            return self._error_body(response)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str):
                    return self.redact(message)[:MAX_ERROR_BODY_CHARS]
        return self._error_body(response)

    def _record(
        self,
        endpoint: str,
        attempt: int,
        started: float,
        *,
        status_code: int | None = None,
        error_class: str | None = None,
    ) -> None:
        if self._meter is None:
            return
        record = ApiCallRecord(
            source=self.source,
            endpoint=endpoint,
            attempt=attempt,
            duration_ms=int((time.perf_counter() - started) * 1000),
            status_code=status_code,
            error_class=error_class,
        )
        try:
            self._meter(record)
        except Exception:  # metering must never break the call it is measuring
            logger.exception("could not meter an API call", extra={"source": self.source})
