"""The one scheduler (spec v0.8.0 §3).

Design choice, documented as the spec asks: a small loop in a daemon thread of the RQ
worker process, driven by `croniter`, rather than RQ Scheduler. Reasons: no extra process
and no extra flag to forget in production mode; the schedule is data in this file, so
`docs/operations.md` can list it; and a tick is trivial to test without a clock — `tick(now)`
is a pure function of Redis state plus the time it is handed.

What a tick does: it takes (or renews) a Redis lock so that **only one** scheduler
instance runs even if two workers are started; for every job whose cron expression has a
fire time between its last recorded run and now it creates a `job_run` of kind
`scheduled:<name>` and hands it to RQ like any other run. The RQ worker then executes it
through the normal handler, retry and audit machinery, so every scheduled execution is
visible on `/jobs/{id}/status`, on the health page and in the audit log.

Two guards keep a restart from doing damage: a job with no recorded last run is not
fired retroactively (its clock starts at boot), and a job whose previous run is still
queued or running is not queued again.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from redis import Redis
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.modules.jobs.models import TERMINAL_RUN_STATUSES, JobRun

logger = get_logger("app.scheduler")

SCHEDULED_KIND_PREFIX = "scheduled:"
LOCK_KEY = "scheduler:lock"
LOCK_TTL_SECONDS = 90
LAST_RUN_KEY = "scheduler:last-run"

JOB_CRM_SYNC = "crm-sync"
JOB_PURGE_EXPIRED = "purge-expired"
JOB_BACKUP = "backup"
JOB_BACKUP_VERIFY = "backup-verify"
JOB_WATCHDOG = "watchdog"


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    cron: str
    description: str

    @property
    def kind(self) -> str:
        return f"{SCHEDULED_KIND_PREFIX}{self.name}"


def cron_from_time(value: str) -> str:
    """`"02:00"` → `"0 2 * * *"`. A bad value is a configuration error, raised at startup."""
    try:
        hours, minutes = value.strip().split(":")
        hour, minute = int(hours), int(minutes)
    except ValueError as exc:
        raise ValueError(f"expected HH:MM, got {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"expected HH:MM, got {value!r}")
    return f"{minute} {hour} * * *"


def schedule(settings: Settings | None = None) -> list[ScheduledJob]:
    """The five jobs, with their times read from settings. Documented in docs/operations.md."""
    config = settings or get_settings()
    jobs = [
        ScheduledJob(JOB_CRM_SYNC, "* * * * *", "Send CRM leads whose undo window has closed"),
        ScheduledJob(
            JOB_WATCHDOG,
            "*/5 * * * *",
            "Fail runs stuck in `running` and re-evaluate the alert rules",
        ),
        ScheduledJob(
            JOB_PURGE_EXPIRED,
            cron_from_time(config.purge_at),
            "Drop expired Places content, audit page text and raw AI output",
        ),
        ScheduledJob(JOB_BACKUP, cron_from_time(config.backup_at), "pg_dump into BACKUP_DIR"),
        ScheduledJob(
            JOB_BACKUP_VERIFY,
            config.backup_verify_cron,
            "Restore the newest dump into a throw-away database and check it",
        ),
    ]
    for job in jobs:
        if not croniter.is_valid(job.cron):
            raise ValueError(f"scheduled job '{job.name}' has an invalid cron '{job.cron}'")
    return jobs


class Scheduler:
    """One instance's view of the schedule. `tick()` is what the thread calls repeatedly."""

    def __init__(
        self,
        redis: Redis,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        instance_id: str | None = None,
        enqueue: Callable[[ScheduledJob], uuid.UUID | None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.redis = redis
        self.jobs = schedule(self.settings)
        self.zone = ZoneInfo(self.settings.timezone)
        self._clock = clock or (lambda: datetime.now(self.zone))
        self.instance_id = instance_id or uuid.uuid4().hex
        self._enqueue = enqueue or enqueue_scheduled_run
        self.holds_lock = False

    # --- the single-instance lock ----------------------------------------------------

    def acquire_lock(self) -> bool:
        """Take the lock, or renew it if this instance already holds it."""
        if self.redis.set(LOCK_KEY, self.instance_id, nx=True, ex=LOCK_TTL_SECONDS):
            self.holds_lock = True
            return True
        holder = self.redis.get(LOCK_KEY)
        holder_id = holder.decode() if isinstance(holder, bytes) else holder
        if holder_id == self.instance_id:
            self.redis.expire(LOCK_KEY, LOCK_TTL_SECONDS)
            self.holds_lock = True
            return True
        self.holds_lock = False
        return False

    def release_lock(self) -> None:
        holder = self.redis.get(LOCK_KEY)
        holder_id = holder.decode() if isinstance(holder, bytes) else holder
        if holder_id == self.instance_id:
            self.redis.delete(LOCK_KEY)
        self.holds_lock = False

    # --- when a job is due ------------------------------------------------------------

    def _last_run(self, job: ScheduledJob) -> datetime | None:
        raw = self.redis.hget(LAST_RUN_KEY, job.name)
        if not isinstance(raw, bytes | str):
            return None
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            return datetime.fromisoformat(text).astimezone(self.zone)
        except ValueError:
            return None

    def _mark_run(self, job: ScheduledJob, when: datetime) -> None:
        self.redis.hset(LAST_RUN_KEY, job.name, when.isoformat())

    def is_due(self, job: ScheduledJob, now: datetime) -> bool:
        """Whether a fire time of `job` falls after its last run and at or before `now`."""
        last = self._last_run(job)
        if last is None:
            # First sight of this job (fresh install or a Redis reset): its clock starts
            # now, so a restart never replays a night of missed backups.
            self._mark_run(job, now)
            return False
        following = croniter(job.cron, last).get_next(datetime)
        return bool(following <= now)

    def tick(self, now: datetime | None = None) -> list[uuid.UUID]:
        """One look at the clock. Returns the ids of the runs it queued."""
        moment = now or self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.zone)
        if not self.acquire_lock():
            return []
        queued: list[uuid.UUID] = []
        for job in self.jobs:
            if not self.is_due(job, moment):
                continue
            self._mark_run(job, moment)
            run_id = self._enqueue(job)
            if run_id is not None:
                queued.append(run_id)
        return queued

    def run_forever(self, stop: threading.Event) -> int:
        """Tick until `stop` is set. A failing tick is logged; the next one gets its turn."""
        ticks = 0
        interval = max(float(self.settings.scheduler_tick_seconds), 1.0)
        try:
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:
                    logger.exception("scheduler tick failed")
                ticks += 1
                stop.wait(interval)
        finally:
            self.release_lock()
        return ticks


def enqueue_scheduled_run(job: ScheduledJob) -> uuid.UUID | None:
    """Create the `scheduled:<name>` run unless one is already queued or running."""
    from app.modules.jobs.service import enqueue_run

    with session_scope() as session:
        pending = session.scalars(
            select(JobRun.id).where(
                JobRun.kind == job.kind, JobRun.status.not_in(TERMINAL_RUN_STATUSES)
            )
        ).first()
        if pending is not None:
            logger.info(
                "scheduled job skipped: its previous run has not finished",
                extra={"job": job.name, "job_run_id": str(pending)},
            )
            return None
        run = enqueue_run(session, search_job_id=None, kind=job.kind, params={"job": job.name})
        logger.info("scheduled job queued", extra={"job": job.name, "job_run_id": str(run.id)})
        return run.id


def start_scheduler_thread(redis: Redis) -> tuple[threading.Thread, threading.Event]:
    """Start the loop beside the RQ worker. Returns the thread and the event that stops it."""
    stop = threading.Event()
    scheduler = Scheduler(redis)
    thread = threading.Thread(
        target=scheduler.run_forever, args=(stop,), name="scheduler", daemon=True
    )
    thread.start()
    logger.info(
        "scheduler started",
        extra={
            "jobs": {job.name: job.cron for job in scheduler.jobs},
            "timezone": scheduler.settings.timezone,
            "tick_seconds": scheduler.settings.scheduler_tick_seconds,
        },
    )
    return thread, stop


__all__ = [
    "JOB_BACKUP",
    "JOB_BACKUP_VERIFY",
    "JOB_CRM_SYNC",
    "JOB_PURGE_EXPIRED",
    "JOB_WATCHDOG",
    "SCHEDULED_KIND_PREFIX",
    "ScheduledJob",
    "Scheduler",
    "cron_from_time",
    "schedule",
    "start_scheduler_thread",
]
