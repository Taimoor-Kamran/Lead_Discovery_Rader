"""v0.15.0: an audit run with several businesses in flight at once.

What must not change: the set of audits a run stores, its summary, one failure staying
one business's, `RunTimedOut` never becoming a failed audit, and cancellation stopping
between businesses rather than halfway through one. All fetches are scripted; no test
here makes a network call.
"""

import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.modules.audit_web import service, worker
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import AUDIT_JOB_KIND
from app.modules.normalization.schemas import WebsiteKind
from app.workers import tasks
from app.workers.timeouts import RunTimedOut
from tests.integration.test_audit_run import audit_run, pipeline, run_with
from tests.integration.test_website_audits_api import make_business, tools

NAMES = [f"Plumber {n}" for n in range(6)]
# Taken once, at import: a wrapper built after another is patched in must not wrap it.
REAL_AUDIT = service.audit_business


def concurrency(monkeypatch: pytest.MonkeyPatch, workers: int) -> None:
    monkeypatch.setattr(get_settings(), "audit_concurrency", workers)


def join_audit_threads() -> None:
    """Wait out any audit thread a test left running, so it cannot touch the next test."""
    for thread in threading.enumerate():
        if thread.name.startswith(f"{worker.THREAD_PREFIX}_"):
            thread.join(timeout=30)


@pytest.fixture(autouse=True)
def _no_stray_threads() -> Iterator[None]:
    yield
    join_audit_threads()


class InFlight:
    """Wraps `audit_business`: counts audits in flight at once and staggers their ends."""

    def __init__(self, delays: dict[str, float] | None = None) -> None:
        self.real = REAL_AUDIT
        self.delays = delays or {}
        self.now = 0
        self.most = 0
        self.sessions: set[int] = set()
        self.lock = threading.Lock()

    def __call__(self, session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
            self.sessions.add(id(session))
        try:
            time.sleep(self.delays.get(business.display_name, 0.05))
            if business.display_name == "Explodes On Audit":
                raise RuntimeError("the parser fell over")
            return self.real(session, business, **kwargs)
        finally:
            with self.lock:
                self.now -= 1


def seed(db: Session) -> tuple[list[Business], JobRun]:
    businesses = [make_business(db, name=name) for name in NAMES]
    businesses.append(make_business(db, name="Explodes On Audit"))
    businesses.append(
        make_business(db, name="No Site Of Its Own", website=None, kind=WebsiteKind.none)
    )
    _, resolution = pipeline(db, businesses)
    db.commit()
    return businesses, resolution


def stored(db: Session) -> dict[str, tuple[AuditStatus, tuple[str, ...]]]:
    db.expire_all()
    rows = db.execute(select(Business.display_name, WebsiteAudit).join(WebsiteAudit)).all()
    return {name: (audit.status, tuple(sorted(audit.finding_codes))) for name, audit in rows}


def test_four_at_once_stores_the_same_audits_and_summary_as_one_at_a_time(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, resolution = seed(db)

    concurrency(monkeypatch, 1)
    one = InFlight()
    monkeypatch.setattr(service, "audit_business", one)
    serial = run_with(db, audit_run(db, resolution), tools(), monkeypatch)
    serial_rows = stored(db)

    db.execute(delete(WebsiteAudit))
    db.commit()

    concurrency(monkeypatch, 4)
    # Reverse the finishing order: the first handed out finishes last.
    four = InFlight({name: 0.05 * (len(NAMES) - i) for i, name in enumerate(NAMES)})
    monkeypatch.setattr(service, "audit_business", four)
    parallel = run_with(db, audit_run(db, resolution), tools(), monkeypatch)

    assert one.most == 1, "at 1 it is the old loop"
    assert four.most == 4, "at 4, four businesses really were in flight together"
    assert stored(db) == serial_rows
    assert parallel.result_summary == serial.result_summary
    assert parallel.result_summary == {
        "audited": 6,
        "skipped": 1,
        "robots_blocked": 0,
        "bot_challenge": 0,
        "not_readable": 0,
        "unreachable": 0,
        "failed": 1,
        "psi_calls": 6,
    }
    assert (parallel.progress_done, parallel.progress_total) == (8, 8)


def test_one_failure_stays_that_business_s_own_row_under_concurrency(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, resolution = seed(db)
    concurrency(monkeypatch, 4)
    monkeypatch.setattr(service, "audit_business", InFlight())

    run_with(db, audit_run(db, resolution), tools(), monkeypatch)

    rows = stored(db)
    assert rows["Explodes On Audit"][0] is AuditStatus.failed
    assert all(rows[name][0] is AuditStatus.done for name in NAMES), "the others unaffected"
    failed = db.scalars(select(WebsiteAudit).where(WebsiteAudit.status == AuditStatus.failed)).one()
    assert "RuntimeError: the parser fell over" in failed.checks["error"]["value"]
    assert failed.findings == []


def test_a_half_written_row_rolls_back_with_its_own_business_only(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the savepoint did, now done by each thread's own transaction."""
    _, resolution = seed(db)
    concurrency(monkeypatch, 4)
    real = service.audit_business

    def half_writes(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        if business.display_name == "Explodes On Audit":
            session.add(Business(display_name="a half-written row"))
            session.flush()
            raise RuntimeError("boom")
        return real(session, business, **kwargs)

    monkeypatch.setattr(service, "audit_business", half_writes)
    run_with(db, audit_run(db, resolution), tools(), monkeypatch)

    names = set(db.scalars(select(Business.display_name)))
    assert "a half-written row" not in names
    assert len(stored(db)) == len(NAMES) + 2


def test_each_audit_gets_a_business_id_and_a_session_of_its_own(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, resolution = seed(db)
    concurrency(monkeypatch, 4)
    handed: list[object] = []
    real_one = worker._audit_one

    def recording(business_id: object, **kwargs: Any) -> Any:
        handed.append(business_id)
        return real_one(business_id, **kwargs)  # type: ignore[arg-type]

    in_flight = InFlight()
    monkeypatch.setattr(worker, "_audit_one", recording)
    monkeypatch.setattr(service, "audit_business", in_flight)
    run_with(db, audit_run(db, resolution), tools(), monkeypatch)

    assert len(handed) == len(NAMES) + 2
    assert all(isinstance(item, uuid.UUID) for item in handed), "ids, never ORM objects"
    assert id(db) not in in_flight.sessions, "no audit ran in the run's own session"
    assert len(in_flight.sessions) >= 4


def test_progress_rises_by_one_per_finished_audit_to_exactly_the_total(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, resolution = seed(db)
    concurrency(monkeypatch, 4)
    monkeypatch.setattr(service, "audit_business", InFlight())
    seen: list[int] = []
    real = tasks.checkpoint
    caller: list[str] = []

    def recording(session: Session, run: JobRun, *, done: int | None = None) -> None:
        caller.append(threading.current_thread().name)
        if done is not None:
            seen.append(done)
        real(session, run, done=done)

    monkeypatch.setattr(tasks, "checkpoint", recording)
    run = run_with(db, audit_run(db, resolution), tools(), monkeypatch)

    assert seen == list(range(len(NAMES) + 3)), "0, then one more per finished audit"
    assert run.progress_done == run.progress_total == len(NAMES) + 2
    assert set(caller) == {threading.main_thread().name}, "workers never touch the run"


# --- cancellation -------------------------------------------------------------------------


def test_a_cancel_lets_in_flight_audits_finish_and_starts_no_new_one(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    businesses = [make_business(db, name=name) for name in NAMES]
    _, resolution = pipeline(db, businesses)
    db.commit()
    run = audit_run(db, resolution)
    concurrency(monkeypatch, 4)

    real = service.audit_business
    started: list[str] = []
    first_done = threading.Event()
    lock = threading.Lock()

    def cancel_after_the_first(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        with lock:
            started.append(business.display_name)
            first = len(started) == 1
        if not first:
            first_done.wait(timeout=10)  # held in flight until the cancel is on record
            time.sleep(0.05)
        result = real(session, business, **kwargs)
        if first:
            # What the API does: set the flag from outside, in its own transaction.
            with session_scope() as other:
                other.execute(
                    update(JobRun).where(JobRun.id == run.id).values(cancel_requested=True)
                )
            first_done.set()
        return result

    monkeypatch.setattr(service, "audit_business", cancel_after_the_first)
    with pytest.raises(tasks.JobCancelledError):
        run_with(db, run, tools(), monkeypatch)
    join_audit_threads()

    assert len(started) == 4, f"only the four in flight ran: {started}"
    audits = list(db.scalars(select(WebsiteAudit)))
    assert len(audits) == 4, "each in-flight audit finished and was stored"
    assert all(a.status is AuditStatus.done and a.finding_codes for a in audits), "no partial row"
    db.expire_all()
    stored_run = db.get(JobRun, run.id)
    assert stored_run is not None and stored_run.progress_done == 4


# --- the run's time limit -----------------------------------------------------------------


def _queued_run(db: Session, resolution: JobRun) -> JobRun:
    run = JobRun(
        kind=AUDIT_JOB_KIND,
        status=JobRunStatus.queued,
        params={"parent_run_id": str(resolution.id)},
    )
    db.add(run)
    db.commit()
    return run


def test_the_time_limit_on_the_main_thread_escapes_and_no_audit_is_written_as_failed(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RQ's limit is a signal, so in production it lands on the main thread while it waits
    for results — not in a worker. Simulated by the wait itself raising it."""
    businesses = [make_business(db, name=name) for name in NAMES]
    _, resolution = pipeline(db, businesses)
    run = _queued_run(db, resolution)
    concurrency(monkeypatch, 4)

    release = threading.Event()
    real = service.audit_business

    def held(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        release.wait(timeout=10)
        return real(session, business, **kwargs)

    def alarm(*args: Any, **kwargs: Any) -> Any:
        raise RunTimedOut(180)

    monkeypatch.setattr(service, "audit_business", held)
    monkeypatch.setattr(service.AuditTools, "build", classmethod(lambda cls, **kwargs: tools()))
    monkeypatch.setattr(worker, "wait", alarm)

    started = time.monotonic()
    with pytest.raises(RunTimedOut):
        tasks.execute_job_run(run.id, sleeper=lambda seconds: None)
    assert time.monotonic() - started < 5, "the pool was not waited for past the limit"

    release.set()
    join_audit_threads()
    db.expire_all()
    stored_run = db.get(JobRun, run.id)
    assert stored_run is not None
    assert stored_run.status is JobRunStatus.failed
    assert stored_run.attempts == 1
    assert stored_run.error is not None and "time limit of 180 s" in stored_run.error
    statuses = [a.status for a in db.scalars(select(WebsiteAudit))]
    assert AuditStatus.failed not in statuses, "no business written as a failed audit"
    assert len(statuses) <= 4, "nothing past the four in flight was started"


def test_the_time_limit_raised_inside_a_worker_also_escapes_the_pool(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    businesses = [make_business(db, name=name) for name in NAMES]
    _, resolution = pipeline(db, businesses)
    run = _queued_run(db, resolution)
    concurrency(monkeypatch, 4)
    real = service.audit_business

    def limit_on_one(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        if business.display_name == NAMES[2]:
            raise RunTimedOut(180)
        return real(session, business, **kwargs)

    monkeypatch.setattr(service, "audit_business", limit_on_one)
    monkeypatch.setattr(service.AuditTools, "build", classmethod(lambda cls, **kwargs: tools()))

    with pytest.raises(RunTimedOut):
        tasks.execute_job_run(run.id, sleeper=lambda seconds: None)
    join_audit_threads()

    db.expire_all()
    stored_run = db.get(JobRun, run.id)
    assert stored_run is not None and stored_run.status is JobRunStatus.failed
    target = db.scalars(select(Business).where(Business.display_name == NAMES[2])).one()
    assert service.latest_audit(db, target.id) is None, "no false 'failed audit'"
    statuses = [a.status for a in db.scalars(select(WebsiteAudit))]
    assert AuditStatus.failed not in statuses


# --- C6 -----------------------------------------------------------------------------------


def test_more_businesses_at_once_than_fetch_slots_is_warned_about_at_run_start(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, resolution = pipeline(db, [make_business(db, name="Only One")])
    db.commit()
    concurrency(monkeypatch, 6)
    monkeypatch.setattr(get_settings(), "audit_max_concurrency", 4)

    with caplog.at_level("WARNING", logger="app.audit_web.worker"):
        run_with(db, audit_run(db, resolution), tools(), monkeypatch)

    [warning] = [r for r in caplog.records if "AUDIT_CONCURRENCY" in r.getMessage()]
    message = warning.getMessage()
    assert "AUDIT_CONCURRENCY (6) exceeds AUDIT_MAX_CONCURRENCY (4)" in message
    assert "up to 30 seconds" in message and "proceeds anyway" in message


def test_no_warning_when_the_fetch_slots_suffice(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, resolution = pipeline(db, [make_business(db, name="Only One")])
    db.commit()
    concurrency(monkeypatch, 4)
    monkeypatch.setattr(get_settings(), "audit_max_concurrency", 4)

    with caplog.at_level("WARNING", logger="app.audit_web.worker"):
        run_with(db, audit_run(db, resolution), tools(), monkeypatch)

    assert not [r for r in caplog.records if "AUDIT_CONCURRENCY" in r.getMessage()]
