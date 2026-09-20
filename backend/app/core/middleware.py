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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """The response headers every API answer carries (spec v0.8.0 §5).

    `Cache-Control: no-store` goes on authenticated answers (a bearer token or our refresh
    cookie was in the request), so nothing a signed-in user saw is left in a shared cache.
    `Strict-Transport-Security` is added only when the request arrived over https: on a
    plain-http 127.0.0.1 deployment it would be wrong, and a browser would remember it.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if _is_authenticated(request) or "set-cookie" in headers:
            headers["Cache-Control"] = "no-store"
            headers.setdefault("Pragma", "no-cache")
        if _is_https(request):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _is_authenticated(request: Request) -> bool:
    if request.headers.get("authorization", "").strip():
        return True
    return bool(request.cookies)


def _is_https(request: Request) -> bool:
    """Only the connection's own scheme counts: no proxy sits in front of this deployment,
    so a forwarded-proto header is not trusted (that is the v1.1 VPS spec's business)."""
    return request.url.scheme == "https"


def _is_safe_id(value: str) -> bool:
    return 0 < len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)


def get_request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else "-"
