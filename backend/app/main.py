"""FastAPI application factory and the exception handlers that build the error envelope."""

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import models_registry  # noqa: F401
from app.core.config import get_settings
from app.core.errors import AppError, ErrorBody, ErrorEnvelope
from app.core.health import health_router
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestIdMiddleware, get_request_id
from app.core.startup import check_startup
from app.modules.ai.router import ai_router
from app.modules.audit_web.router import (
    business_audits_router,
    job_audits_router,
    website_audits_router,
)
from app.modules.auth.router import auth_router, users_router
from app.modules.businesses.router import businesses_router
from app.modules.compliance.router import suppressions_router
from app.modules.crm.router import crm_router
from app.modules.discovery.router import discovered_records_router, job_records_router
from app.modules.jobs.router import jobs_router, search_jobs_router
from app.modules.monitoring.router import admin_router
from app.modules.opportunities.router import (
    business_opportunities_router,
    job_classification_router,
    opportunities_router,
)
from app.modules.resolution.router import job_resolution_router, match_candidates_router
from app.modules.review.router import (
    decisions_router,
    leads_router,
    review_queue_router,
    review_router,
)
from app.modules.sources.router import sources_router

logger = get_logger("app")

_HTTP_CODE_NAMES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_failed",
    429: "rate_limited",
}


def _envelope(
    request: Request, status_code: int, code: str, message: str, details: dict[str, Any]
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code, message=message, request_id=get_request_id(request), details=details
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    check_startup(settings)

    app = FastAPI(
        title="Lead Discovery Radar API",
        version="0.8.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return _envelope(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _envelope(
            request,
            422,
            "validation_failed",
            "The request body or parameters are not valid",
            {"errors": _clean_validation_errors(exc.errors())},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODE_NAMES.get(exc.status_code, "http_error")
        return _envelope(request, exc.status_code, code, str(exc.detail), {})

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"path": request.url.path})
        return _envelope(request, 500, "internal_error", "An unexpected error occurred", {})

    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(auth_router, prefix=settings.api_v1_prefix)
    app.include_router(users_router, prefix=settings.api_v1_prefix)
    app.include_router(search_jobs_router, prefix=settings.api_v1_prefix)
    app.include_router(jobs_router, prefix=settings.api_v1_prefix)
    app.include_router(job_records_router, prefix=settings.api_v1_prefix)
    app.include_router(sources_router, prefix=settings.api_v1_prefix)
    app.include_router(discovered_records_router, prefix=settings.api_v1_prefix)
    app.include_router(businesses_router, prefix=settings.api_v1_prefix)
    app.include_router(match_candidates_router, prefix=settings.api_v1_prefix)
    app.include_router(job_resolution_router, prefix=settings.api_v1_prefix)
    app.include_router(business_audits_router, prefix=settings.api_v1_prefix)
    app.include_router(job_audits_router, prefix=settings.api_v1_prefix)
    app.include_router(website_audits_router, prefix=settings.api_v1_prefix)
    app.include_router(opportunities_router, prefix=settings.api_v1_prefix)
    app.include_router(business_opportunities_router, prefix=settings.api_v1_prefix)
    app.include_router(job_classification_router, prefix=settings.api_v1_prefix)
    app.include_router(ai_router, prefix=settings.api_v1_prefix)
    app.include_router(review_queue_router, prefix=settings.api_v1_prefix)
    app.include_router(review_router, prefix=settings.api_v1_prefix)
    app.include_router(decisions_router, prefix=settings.api_v1_prefix)
    app.include_router(leads_router, prefix=settings.api_v1_prefix)
    app.include_router(suppressions_router, prefix=settings.api_v1_prefix)
    app.include_router(crm_router, prefix=settings.api_v1_prefix)
    app.include_router(admin_router, prefix=settings.api_v1_prefix)
    return app


def _clean_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Strip the raw input out of validation errors so a password can never be echoed."""
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        item = {k: v for k, v in dict(error).items() if k not in {"input", "ctx", "url"}}
        item["loc"] = [str(part) for part in item.get("loc", [])]
        cleaned.append(item)
    return cleaned


app = create_app()
