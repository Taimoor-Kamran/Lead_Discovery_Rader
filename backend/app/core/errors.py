"""Error envelope and application exception types.

Every error response has the shape:
    {"error": {"code", "message", "request_id", "details"}}
"""

from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """Base class for errors that map onto the error envelope."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self, message: str, *, details: dict[str, Any] | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code is not None:
            self.code = code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class InvalidStateTransitionError(ConflictError):
    code = "invalid_state_transition"


class TooManyRequestsError(AppError):
    status_code = 429
    code = "rate_limited"
