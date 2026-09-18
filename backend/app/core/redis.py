"""Redis connection and the RQ queue used by the job framework."""

from typing import Any

from redis import Redis
from rq import Queue

from app.core.config import get_settings

_redis: Redis | None = None
_queue: Queue | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url)
    return _redis


def set_redis(client: Any) -> None:
    """Swap the connection (tests use fakeredis). Also resets the cached queue."""
    global _redis, _queue
    _redis = client
    _queue = None


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        settings = get_settings()
        _queue = Queue(
            settings.job_queue_name,
            connection=get_redis(),
            is_async=settings.job_queue_is_async,
        )
    return _queue


def set_queue(queue: Queue | None) -> None:
    global _queue
    _queue = queue


def redis_ok() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        # A health check reports "not ok"; it never raises.
        return False
