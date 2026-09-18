"""Liveness/readiness endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import get_engine
from app.core.logging import get_logger
from app.core.redis import redis_ok

logger = get_logger("app.health")
health_router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: bool
    redis: bool


def _db_ok() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("database health check failed")
        return False
    return True


@health_router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db = _db_ok()
    redis = redis_ok()
    return HealthResponse(status="ok" if db and redis else "degraded", db=db, redis=redis)
