"""Request-ID correlation and access logging."""

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_ctx

REQUEST_ID_HEADER = "X-Request-ID"
logger = get_logger("app.request")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns every request an id, exposes it on `request.state` and in the response."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _is_safe_id(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request handled",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response


def _is_safe_id(value: str) -> bool:
    return 0 < len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)


def get_request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else "-"
