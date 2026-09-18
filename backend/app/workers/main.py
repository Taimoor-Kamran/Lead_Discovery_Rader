"""RQ worker entrypoint: `python -m app.workers.main`."""

from rq import Worker

from app import models_registry  # noqa: F401
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis


def main() -> None:
    configure_logging()
    settings = get_settings()
    logger = get_logger("app.worker")
    logger.info("worker starting", extra={"queue": settings.job_queue_name})
    worker = Worker([settings.job_queue_name], connection=get_redis())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
