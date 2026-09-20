"""CRM sync entrypoints for the worker.

Since v0.8.0 the minute-by-minute `sync_due()` is one of the scheduler's jobs
(`scheduled:crm-sync`, see `app/workers/scheduler.py`), so every tick is a visible job
run. `run_sync_loop` / `start_sync_thread` are the v0.7.0 stand-alone loop, kept for a
worker started with `SCHEDULER_ENABLED=false`; `sync_crm_lead` stays an RQ-able entrypoint
for a one-off enqueue. One tick = one `sync_due()`, each lead committed on its own so a
failure never holds back the next one.
"""

import threading
import uuid
from collections.abc import Callable

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.modules.crm import service
from app.modules.crm.models import CrmLeadStatus

logger = get_logger("app.crm.worker")


def sync_crm_lead(crm_lead_id: str | uuid.UUID) -> CrmLeadStatus:
    """Sync one lead now, in its own transaction. Safe to enqueue on RQ."""
    with session_scope() as session:
        lead = service.get_crm_lead(session, uuid.UUID(str(crm_lead_id)))
        service.sync_lead(session, lead)
        return lead.status


def sync_due() -> int:
    """One tick: every scheduled lead whose `due_at` has passed."""
    with session_scope() as session:
        return service.sync_due(session)


def run_sync_loop(
    stop: threading.Event,
    *,
    interval_seconds: float | None = None,
    tick: Callable[[], int] = sync_due,
    sleeper: Callable[[float], None] | None = None,
) -> int:
    """Call `tick` until `stop` is set. Returns how many ticks ran. Never lets a tick's
    exception end the loop: the next minute gets another chance."""
    settings = get_settings()
    interval = (
        interval_seconds if interval_seconds is not None else settings.crm_sync_interval_seconds
    )
    wait = sleeper if sleeper is not None else (lambda seconds: stop.wait(seconds))
    ticks = 0
    while not stop.is_set():
        try:
            processed = tick()
            if processed:
                logger.info("crm sync tick", extra={"processed": processed})
        except Exception:
            logger.exception("crm sync tick failed")
        ticks += 1
        wait(float(interval))
    return ticks


def start_sync_thread() -> tuple[threading.Thread, threading.Event]:
    """Start the loop beside the RQ worker. Returns the thread and the event that stops it."""
    stop = threading.Event()
    thread = threading.Thread(
        target=run_sync_loop, kwargs={"stop": stop}, name="crm-sync", daemon=True
    )
    thread.start()
    logger.info(
        "crm sync loop started",
        extra={"interval_seconds": get_settings().crm_sync_interval_seconds},
    )
    return thread, stop
