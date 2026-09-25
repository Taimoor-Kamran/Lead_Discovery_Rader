"""RQ worker entrypoint: `python -m app.workers.main`."""

from app import models_registry  # noqa: F401
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis
from app.core.startup import check_startup
from app.workers.timeouts import RadarWorker


def main() -> None:
    configure_logging()
    settings = get_settings()
    check_startup(settings)
    logger = get_logger("app.worker")
    logger.info("worker starting", extra={"queue": settings.job_queue_name})
    if settings.scheduler_enabled:
        # One scheduler for everything periodic (CRM sync, purge, backups, verify,
        # watchdog). A Redis lock keeps a second worker from running a second one.
        from app.workers.scheduler import start_scheduler_thread

        start_scheduler_thread(get_redis())
    worker = RadarWorker([settings.job_queue_name], connection=get_redis())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
