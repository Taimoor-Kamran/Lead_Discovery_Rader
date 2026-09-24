"""The scheduler (spec v0.8.0 §3): five jobs, one instance, every execution a job run."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.alerts import service as alerts
from app.modules.alerts.models import Alert
from app.modules.audit.models import AuditLog
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import DEMO_JOB_KIND, enqueue_run
from app.modules.jobs.state import transition
from app.workers import tasks
from app.workers.scheduled import QUEUE_LOST, WORKER_LOST, run_watchdog
from app.workers.scheduler import (
    JOB_BACKUP,
    JOB_BACKUP_VERIFY,
    JOB_CRM_SYNC,
    JOB_PURGE_EXPIRED,
    JOB_WATCHDOG,
    LOCK_KEY,
    SCHEDULED_KIND_PREFIX,
    ScheduledJob,
    Scheduler,
    cron_from_time,
    schedule,
)

T0 = datetime(2026, 9, 21, 1, 59, 30, tzinfo=UTC)


def test_the_schedule_has_the_five_jobs_at_the_configured_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKUP_AT", "02:30")
    monkeypatch.setenv("PURGE_AT", "03:00")
    monkeypatch.setenv("BACKUP_VERIFY_CRON", "0 4 * * 0")
    get_settings.cache_clear()

    by_name = {job.name: job for job in schedule()}

    assert set(by_name) == {
        JOB_CRM_SYNC,
        JOB_WATCHDOG,
        JOB_PURGE_EXPIRED,
        JOB_BACKUP,
        JOB_BACKUP_VERIFY,
    }
    assert by_name[JOB_CRM_SYNC].cron == "* * * * *"
    assert by_name[JOB_WATCHDOG].cron == "*/5 * * * *"
    assert by_name[JOB_PURGE_EXPIRED].cron == "0 3 * * *"
    assert by_name[JOB_BACKUP].cron == "30 2 * * *"
    assert by_name[JOB_BACKUP_VERIFY].cron == "0 4 * * 0"
    assert by_name[JOB_BACKUP].kind == "scheduled:backup"


@pytest.mark.parametrize("value", ["2:00", "24:00", "02", "02:60", "noon"])
def test_a_bad_time_is_a_configuration_error(value: str) -> None:
    if value == "2:00":
        assert cron_from_time(value) == "0 2 * * *"
        return
    with pytest.raises(ValueError, match="HH:MM"):
        cron_from_time(value)


def _scheduler(
    redis: fakeredis.FakeStrictRedis, queued: list[str], *, instance_id: str = "a"
) -> Scheduler:
    def enqueue(job: ScheduledJob) -> uuid.UUID:
        queued.append(job.name)
        return uuid.uuid4()

    return Scheduler(redis, instance_id=instance_id, enqueue=enqueue)


def test_a_job_fires_once_per_cron_slot_and_never_retroactively_on_first_boot() -> None:
    redis = fakeredis.FakeStrictRedis()
    queued: list[str] = []
    scheduler = _scheduler(redis, queued)

    # First tick: every job's clock starts now; nothing is replayed.
    assert scheduler.tick(T0) == []
    assert queued == []

    # 02:00:10 — the minute rolled over and the 02:00 backup slot passed.
    scheduler.tick(T0 + timedelta(seconds=40))
    assert sorted(queued) == sorted([JOB_CRM_SYNC, JOB_BACKUP, JOB_WATCHDOG])

    # Same minute again: nothing new. Ten seconds into 02:01: only the every-minute job.
    queued.clear()
    scheduler.tick(T0 + timedelta(seconds=50))
    assert queued == []
    scheduler.tick(T0 + timedelta(minutes=1, seconds=40))
    assert queued == [JOB_CRM_SYNC]


def test_only_one_instance_runs_at_a_time() -> None:
    redis = fakeredis.FakeStrictRedis()
    queued_a: list[str] = []
    queued_b: list[str] = []
    first = _scheduler(redis, queued_a, instance_id="a")
    second = _scheduler(redis, queued_b, instance_id="b")

    first.tick(T0)
    second.tick(T0)
    first.tick(T0 + timedelta(seconds=40))
    second.tick(T0 + timedelta(seconds=40))

    assert queued_a, "the holder of the lock fires the jobs"
    assert queued_b == [], "the second instance never fires while the lock is held"
    assert first.holds_lock and not second.holds_lock

    # When the holder goes away, the lock expires and the other instance takes over.
    first.release_lock()
    assert redis.get(LOCK_KEY) is None
    second.tick(T0 + timedelta(minutes=1, seconds=40))
    assert queued_b == [JOB_CRM_SYNC]


def test_a_scheduled_run_is_a_job_run_and_is_not_queued_twice(
    db: Session, fake_redis: fakeredis.FakeStrictRedis
) -> None:
    scheduler = Scheduler(fake_redis, instance_id="a")
    scheduler.tick(T0)
    run_ids = scheduler.tick(T0 + timedelta(seconds=40))

    db.expire_all()
    runs = list(db.scalars(select(JobRun).where(JobRun.id.in_(run_ids))))
    assert {run.kind for run in runs} == {
        f"{SCHEDULED_KIND_PREFIX}{JOB_CRM_SYNC}",
        f"{SCHEDULED_KIND_PREFIX}{JOB_BACKUP}",
        f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}",
    }
    assert all(run.status is JobRunStatus.queued for run in runs)
    assert all(run.params == {"job": run.kind.removeprefix(SCHEDULED_KIND_PREFIX)} for run in runs)
    assert "job_run.enqueued" in list(db.scalars(select(AuditLog.action)))

    # A minute later the crm-sync run is still queued (no worker ran it): not queued again.
    again = scheduler.tick(T0 + timedelta(minutes=1, seconds=40))
    assert again == []


def test_the_crm_sync_and_purge_handlers_run_through_the_worker(
    db: Session, fake_redis: fakeredis.FakeStrictRedis
) -> None:
    for name in (JOB_CRM_SYNC, JOB_PURGE_EXPIRED):
        run = enqueue_run(db, search_job_id=None, kind=f"{SCHEDULED_KIND_PREFIX}{name}")
        assert tasks.execute_job_run(run.id) is JobRunStatus.done
        db.expire_all()
        finished = db.get(JobRun, run.id)
        assert finished is not None and finished.result_summary is not None
    processed = db.scalars(
        select(JobRun).where(JobRun.kind == f"{SCHEDULED_KIND_PREFIX}{JOB_CRM_SYNC}")
    ).one()
    assert processed.result_summary == {"processed": 0}
    purged = db.scalars(
        select(JobRun).where(JobRun.kind == f"{SCHEDULED_KIND_PREFIX}{JOB_PURGE_EXPIRED}")
    ).one()
    assert purged.result_summary is not None
    assert set(purged.result_summary) >= {"records", "audit_page_texts", "ai_classifications"}


def _running_since(db: Session, kind: str, started_at: datetime) -> JobRun:
    run = JobRun(kind=kind, status=JobRunStatus.queued)
    db.add(run)
    db.flush()
    transition(db, run, JobRunStatus.running)
    run.started_at = started_at
    run.updated_at = started_at  # and no progress since
    db.commit()
    return run


def test_the_watchdog_fails_a_stuck_run_with_worker_lost_and_alerts(
    db: Session, fake_redis: fakeredis.FakeStrictRedis
) -> None:
    now = datetime.now(UTC)
    stuck = _running_since(db, DEMO_JOB_KIND, now - timedelta(minutes=45))
    fresh = _running_since(db, DEMO_JOB_KIND, now - timedelta(minutes=5))

    run = enqueue_run(db, search_job_id=None, kind=f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}")
    assert tasks.execute_job_run(run.id) is JobRunStatus.done

    db.expire_all()
    assert stuck.status is JobRunStatus.failed
    assert stuck.error == WORKER_LOST
    assert fresh.status is JobRunStatus.running, "a run inside the limit is left alone"
    watchdog = db.get(JobRun, run.id)
    assert watchdog is not None and watchdog.result_summary is not None
    assert watchdog.result_summary["stale_runs_failed"] == 1
    assert watchdog.result_summary["stale"][0]["job_run_id"] == str(stuck.id)

    alert = db.scalars(select(Alert).where(Alert.rule == alerts.RULE_STALE_JOB)).one()
    assert alert.active and not alert.acknowledged
    assert alert.details["job_run_id"] == str(stuck.id)
    assert "worker lost" in alert.message


def test_a_long_run_that_is_still_making_progress_is_left_alone(
    db: Session, fake_redis: fakeredis.FakeStrictRedis
) -> None:
    """Staleness is time since the last progress, not since the start (v0.11.0): a
    54-business audit takes about 30 minutes one at a time and is alive throughout."""
    now = datetime.now(UTC)
    working = _running_since(db, DEMO_JOB_KIND, now - timedelta(minutes=45))
    working.updated_at = now - timedelta(minutes=2)  # a checkpoint two minutes ago
    silent = _running_since(db, DEMO_JOB_KIND, now - timedelta(minutes=45))
    silent.updated_at = now - timedelta(minutes=35)
    db.commit()

    run = enqueue_run(db, search_job_id=None, kind=f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}")
    assert tasks.execute_job_run(run.id) is JobRunStatus.done

    db.expire_all()
    assert working.status is JobRunStatus.running
    assert silent.status is JobRunStatus.failed
    assert silent.error == WORKER_LOST
    assert "no progress" in WORKER_LOST


def test_the_watchdog_never_fails_itself(db: Session, fake_redis: Any) -> None:
    """Its own run is `running` while it works; an old `started_at` must not trip it."""
    run = _running_since(
        db,
        f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}",
        datetime.now(UTC) - timedelta(hours=2),
    )
    run_watchdog(db, run)
    db.commit()
    db.expire_all()
    assert run.status is JobRunStatus.running
    assert run.result_summary == {"stale_runs_failed": 0, "stale": [], "alerts_open": 0}


def test_a_failed_backup_verify_is_not_retried_and_raises_an_alert(
    db: Session, fake_redis: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "empty"))
    get_settings.cache_clear()
    run = enqueue_run(db, search_job_id=None, kind=f"{SCHEDULED_KIND_PREFIX}{JOB_BACKUP_VERIFY}")

    sleeps: list[float] = []
    assert tasks.execute_job_run(run.id, sleeper=sleeps.append) is JobRunStatus.failed

    db.expire_all()
    failed = db.get(JobRun, run.id)
    assert failed is not None
    assert failed.attempts == 1 and sleeps == [], "nothing transient about a bad backup"
    assert failed.error is not None and "no backup to verify" in failed.error
    alert = db.scalars(select(Alert).where(Alert.rule == alerts.RULE_BACKUP_VERIFY_FAILED)).one()
    assert alert.severity == "critical"
    assert alert.active


def test_the_watchdog_fails_an_abandoned_queued_run_and_unblocks_its_schedule(
    db: Session, fake_redis: fakeredis.FakeStrictRedis
) -> None:
    """The v0.9.0 `scheduled:crm-sync` incident, from both ends.

    A run that died before it reached `running` was rolled back to `queued` with no job
    left on the queue. `stale_runs` only ever looked at `running`, so the watchdog walked
    past it, and the scheduler — which skips a job whose previous run has not finished —
    queued no CRM sync for a day. The watchdog now fails runs of *any* kind stuck in
    `queued` past the limit with nothing on the queue, and the next tick queues the job.
    """
    now = datetime.now(UTC)
    kind = f"{SCHEDULED_KIND_PREFIX}{JOB_CRM_SYNC}"
    abandoned = JobRun(kind=kind, status=JobRunStatus.queued)
    abandoned.created_at = now - timedelta(hours=21)
    db.add(abandoned)
    db.commit()
    assert fake_redis.llen("rq:queue:default") == 0, "nothing on the queue will run it"

    run = enqueue_run(db, search_job_id=None, kind=f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}")
    assert tasks.execute_job_run(run.id) is JobRunStatus.done

    db.expire_all()
    assert abandoned.status is JobRunStatus.failed
    assert abandoned.error == QUEUE_LOST
    assert abandoned.is_terminal, "the scheduler can only move on once this run is finished"

    watchdog = db.get(JobRun, run.id)
    assert watchdog is not None and watchdog.result_summary is not None
    assert watchdog.result_summary["stale"] == [{"job_run_id": str(abandoned.id), "kind": kind}]

    # And the schedule is moving again: the next tick queues a new crm-sync run.
    scheduler = Scheduler(fake_redis, instance_id="after-the-watchdog")
    scheduler.tick(T0)
    queued = scheduler.tick(T0 + timedelta(minutes=1, seconds=40))
    db.expire_all()
    kinds = {db.get(JobRun, run_id).kind for run_id in queued}  # type: ignore[union-attr]
    assert kind in kinds


def test_the_watchdog_leaves_a_queued_run_that_still_has_its_job_alone(
    db: Session, fake_redis: fakeredis.FakeStrictRedis
) -> None:
    """A busy queue is not a broken one: only a run RQ has forgotten is failed."""
    waiting = enqueue_run(db, search_job_id=None, kind=DEMO_JOB_KIND)
    waiting.created_at = datetime.now(UTC) - timedelta(hours=3)
    db.commit()

    run = enqueue_run(db, search_job_id=None, kind=f"{SCHEDULED_KIND_PREFIX}{JOB_WATCHDOG}")
    assert tasks.execute_job_run(run.id) is JobRunStatus.done

    db.expire_all()
    assert waiting.status is JobRunStatus.queued
    watchdog = db.get(JobRun, run.id)
    assert watchdog is not None and watchdog.result_summary is not None
    assert watchdog.result_summary["stale_runs_failed"] == 0


def test_every_checkpoint_is_a_heartbeat_even_when_nothing_else_changed(db: Session) -> None:
    """Discovery checkpoints between records without always moving `progress_done`."""
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    run = _running_since(db, DEMO_JOB_KIND, hour_ago)

    tasks.checkpoint(db, run)

    db.expire_all()
    assert run.updated_at > hour_ago + timedelta(minutes=59)
