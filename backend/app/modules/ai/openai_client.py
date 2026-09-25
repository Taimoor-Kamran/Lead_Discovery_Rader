"""The OpenAI provider, through the official SDK, with strict JSON-schema output.

Timeouts and retries on 429/5xx are the SDK's own (`timeout`, `max_retries`); every call
is metered into `api_calls` under the `openai` source row and rate limited like PageSpeed,
so a runaway run cannot spend faster than the configured budget can notice. The API key
is handed to the SDK once and appears in no log line: the logging module redacts it.
"""

import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import openai

from app.core.config import Settings, get_settings
from app.core.http import ApiCallRecord, Limiter, MeteringHook
from app.core.logging import get_logger
from app.modules.ai.client import LLMError, LLMRequest, LLMResult
from app.modules.sources.models import SourceKind

logger = get_logger("app.ai.openai")

OPENAI_SOURCE_NAME = "openai"
# Marks the `sources` row: an API the pipeline calls for a service, never searched.
AI_SERVICE_ROLE = "ai_service"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
RETRYABLE_STATUSES = frozenset({408, 409, 429})
# How much of a provider error body is kept on the classification row. Long enough for
# the `message`/`param`/`code` triple that names the offending parameter, short enough
# that a stack of them cannot bloat the table.
ERROR_BODY_MAX_CHARS = 600


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _error_body(exc: openai.APIStatusError) -> str:
    """What the provider actually objected to, for the `error` column on the row.

    The first pass stored only "OpenAI answered 400: BadRequestError" for all 14 of the
    production failures and threw the body away — so the one thing that would have named
    the offending parameter (`temperature`) needed a manual reproduction to recover. The
    body is the provenance of the failure and is kept, truncated (spec v0.9.0).
    """
    parts = [str(exc.message)] if getattr(exc, "message", None) else []
    for label in ("param", "code"):
        value = getattr(exc, label, None)
        if value:
            parts.append(f"{label}={value}")
    if not parts:
        # No parsed body — a proxy's HTML error page, say. Take the raw text instead.
        try:
            parts = [exc.response.text]
        except Exception:  # pragma: no cover - a response that cannot be read
            parts = [repr(exc.body)]
    return " ".join(part for part in parts if part).strip()[:ERROR_BODY_MAX_CHARS]


class _ConnectionFailedError(Exception):
    """A connection that could not be made at all: the one failure `complete` waits out."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OpenAIClient:
    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        timeout: float = 60.0,
        max_retries: int = 2,
        meter: MeteringHook | None = None,
        limiter: Limiter | None = None,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
        connection_retry_delays: Sequence[float] = (),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        self._client = openai.OpenAI(
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            base_url=base_url,
            http_client=http_client,
        )
        self._meter = meter
        self._limiter = limiter
        self._connection_retry_delays = list(connection_retry_delays)
        self._sleeper = sleeper
        # Set when a call has waited out every pause and still could not connect; cleared
        # by the next call that does. While set, calls fail without pausing, so a run with
        # the network gone spends the pauses once, not once per business, and stays well
        # inside its JOB_TIMEOUT_SECONDS.
        self._unreachable = False
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions" if base_url else OPENAI_ENDPOINT

    def close(self) -> None:
        self._client.close()

    def complete(self, request: LLMRequest) -> LLMResult:
        """One call, retried after a pause while the network itself is down (spec v0.11.1).

        The SDK already retries a failed connection, but within a second or two. The
        production failures of 2026-09-25 were the host's Wi-Fi going away in Modern
        Standby and taking ~27 s to come back after wake, which that cannot bridge. So a
        connection that could not be made is tried again after each of
        AI_CONNECTION_RETRY_DELAYS_SECONDS. Nothing else is: not a timeout (the request
        may have reached OpenAI and been billed; the SDK has retried it already), and
        never an auth, quota or other status error.
        """
        started = time.perf_counter()
        delays = [] if self._unreachable else self._connection_retry_delays
        attempt = 1
        while True:
            try:
                result = self._attempt(request, attempt)
                self._unreachable = False
                return result
            except _ConnectionFailedError as failed:
                if attempt > len(delays):
                    self._unreachable = bool(self._connection_retry_delays)
                    tries = f" (after {attempt} attempts)" if attempt > 1 else ""
                    raise LLMError(
                        f"{failed.message}{tries}"[:ERROR_BODY_MAX_CHARS],
                        retryable=True,
                        latency_ms=_elapsed_ms(started),
                    ) from failed.__cause__
                delay = delays[attempt - 1]
                logger.warning(
                    "OpenAI could not be reached; trying again",
                    extra={"attempt": attempt, "retry_in_seconds": delay},
                )
                self._sleeper(delay)
                attempt += 1

    def _attempt(self, request: LLMRequest, attempt: int) -> LLMResult:
        if self._limiter is not None:
            self._limiter.acquire()
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=request.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "strict": True,
                        "schema": dict(request.json_schema),
                    },
                },
            )
        except openai.APIStatusError as exc:
            self._record(
                started,
                status_code=exc.status_code,
                error_class=type(exc).__name__,
                attempt=attempt,
            )
            retryable = exc.status_code in RETRYABLE_STATUSES or exc.status_code >= 500
            raise LLMError(
                f"OpenAI answered {exc.status_code}: {type(exc).__name__}: {_error_body(exc)}",
                retryable=retryable,
                status_code=exc.status_code,
                latency_ms=_elapsed_ms(started),
            ) from exc
        except openai.APIConnectionError as exc:
            self._record(started, error_class=type(exc).__name__, attempt=attempt)
            reason = f"OpenAI did not answer: {type(exc).__name__}: {exc}"
            if not isinstance(exc, openai.APITimeoutError):
                raise _ConnectionFailedError(reason) from exc
            raise LLMError(
                reason[:ERROR_BODY_MAX_CHARS],
                retryable=True,
                latency_ms=_elapsed_ms(started),
            ) from exc
        except openai.OpenAIError as exc:
            self._record(started, error_class=type(exc).__name__, attempt=attempt)
            raise LLMError(
                f"OpenAI call failed: {type(exc).__name__}: {exc}"[:ERROR_BODY_MAX_CHARS],
                latency_ms=_elapsed_ms(started),
            ) from exc

        latency_ms = _elapsed_ms(started)
        self._record(started, status_code=200, attempt=attempt)
        if not response.choices:
            raise LLMError("OpenAI returned no choices")
        message = response.choices[0].message
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise LLMError(f"OpenAI refused the request: {str(refusal)[:200]}")
        usage = response.usage
        return LLMResult(
            text=message.content or "",
            model=response.model or request.model,
            tokens_in=int(usage.prompt_tokens) if usage else 0,
            tokens_out=int(usage.completion_tokens) if usage else 0,
            latency_ms=latency_ms,
        )

    def _record(
        self,
        started: float,
        *,
        status_code: int | None = None,
        error_class: str | None = None,
        attempt: int = 1,
    ) -> None:
        if self._meter is None:
            return
        record = ApiCallRecord(
            source=OPENAI_SOURCE_NAME,
            endpoint=self._endpoint,
            attempt=attempt,
            duration_ms=int((time.perf_counter() - started) * 1000),
            status_code=status_code,
            error_class=error_class,
        )
        try:
            self._meter(record)
        except Exception:  # metering must never break the call it is measuring
            logger.exception("could not meter an OpenAI call")


def openai_source_config(settings: Settings | None = None) -> dict[str, Any]:
    """The `sources` row config: the same shape a discovery source has, plus its role."""
    config = settings or get_settings()
    return {
        "role": AI_SERVICE_ROLE,
        "display_name": "OpenAI",
        "terms_url": "https://openai.com/policies/business-terms/",
        "commercial_use_note": (
            "Paid API on the client's account. Receives public homepage text and audit "
            "results only; never a phone number, an email, an address or user data."
        ),
        "content_ttl_days": 0,
        "exclude_from_crm_export": False,
        "rate_limit": {
            "requests_per_second": config.ai_rps,
            "burst": max(int(config.ai_rps), 1),
            "daily_call_cap": config.ai_daily_call_cap,
        },
    }


def build_openai_client(
    *,
    job_run_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    redis_client: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    meter: MeteringHook | None = None,
    base_url: str | None = None,
) -> OpenAIClient:
    """The production wiring: metered, rate limited, the SDK's own retries and timeout."""
    from app.core.ratelimit import build_limiter
    from app.core.redis import get_redis

    config = settings or get_settings()
    if meter is None:
        from app.modules.discovery.service import api_call_meter

        meter = api_call_meter(job_run_id)
    limiter = build_limiter(
        redis_client or get_redis(),
        source=OPENAI_SOURCE_NAME,
        requests_per_second=config.ai_rps,
        burst=max(int(config.ai_rps), 1),
        daily_call_cap=config.ai_daily_call_cap,
        clock=clock,
        sleeper=sleeper,
    )
    return OpenAIClient(
        api_key=config.openai_api_key.get_secret_value(),
        timeout=config.ai_timeout_seconds,
        max_retries=config.ai_max_retries,
        meter=meter,
        limiter=limiter,
        base_url=base_url,
        connection_retry_delays=config.ai_connection_retry_delays_seconds,
        sleeper=sleeper,
    )


def openai_service_source() -> Any:
    from app.modules.audit_web.psi import ServiceSourceSpec

    return ServiceSourceSpec(
        name=OPENAI_SOURCE_NAME, kind=SourceKind.api, config=openai_source_config()
    )
