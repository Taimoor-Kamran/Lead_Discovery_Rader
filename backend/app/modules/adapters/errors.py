"""Failures a source adapter can raise, and whether retrying one can help.

`retryable` is read by the worker: a retryable failure goes back on the queue under the
v0.1.0 backoff policy, a non-retryable one fails the run on its first attempt so an
operator sees the real problem instead of the same error three times.
"""

from typing import Any, ClassVar

from app.core.errors import AppError


class AdapterError(AppError):
    """A source could not be used. Not retryable unless a subclass says otherwise."""

    status_code = 502
    code = "adapter_error"
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        source: str | None = None,
    ) -> None:
        super().__init__(message, details=details, code=code)
        self.source = source


class TransientError(AdapterError):
    """A timeout, a connection failure or a 5xx that survived every retry."""

    status_code = 503
    code = "source_unavailable"
    retryable: ClassVar[bool] = True


class RateLimitedError(AdapterError):
    """The source kept answering 429 after every `Retry-After` had been honoured."""

    status_code = 429
    code = "source_rate_limited"
    retryable: ClassVar[bool] = True


class AuthError(AdapterError):
    """401/403: the credential, the API enablement or billing is wrong. Retrying cannot fix it."""

    status_code = 502
    code = "source_auth_failed"


class SchemaError(AdapterError):
    """The source answered 2xx with a body we cannot parse. The contract has changed."""

    status_code = 502
    code = "source_schema_changed"


class QuotaExceededError(AdapterError):
    """Our own daily call cap would be exceeded. Raised *before* the request is made."""

    status_code = 429
    code = "daily_call_cap_reached"


class RunCallCapExceededError(AdapterError):
    """One run reached its per-run safety limit on calls. Raised *before* the request.

    The limit is a generous multiple of the fewest calls the run could need, so reaching it
    means the run is looping, not busy. Retrying would only run the same loop again.
    """

    status_code = 502
    code = "run_call_cap_reached"
