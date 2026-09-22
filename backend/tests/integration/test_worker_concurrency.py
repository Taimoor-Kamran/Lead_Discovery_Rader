"""The v0.9.0 production incident: the scheduler thread and job execution sharing a socket.

The RQ worker process runs the scheduler in a daemon thread **and** forks a work horse for
every job. Both halves used the one application engine, so a connection the scheduler had
used was copied into a child that then wrote on the same socket. psycopg names its
prepared statements `_pg3_N` per connection and counts them client-side, so the pair
collided:

    psycopg.errors.DuplicatePreparedStatement: prepared statement "_pg3_0" already exists
    psycopg.errors.InvalidSqlStatementName:    prepared statement "_pg3_0" does not exist

That is what `logs/worker.log` shows every five minutes, and what killed a real discovery
run of "plumber in Austin, TX" after four places. These tests hold the three answers in
place: separate engines, a fork that drops what it inherited, and no prepared statements
to collide over in the first place.
"""

import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, QueuePool

from app.core.db import (
    connect_args_for,
    get_engine,
    get_scheduler_engine,
    scheduler_session_scope,
    session_scope,
)
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import DEMO_JOB_KIND, enqueue_run
from app.workers import tasks
from app.workers.scheduler import (
    JOB_CRM_SYNC,
    SCHEDULED_KIND_PREFIX,
    Scheduler,
    enqueue_scheduled_run,
)

T0 = datetime(2026, 9, 21, 19, 53, 0, tzinfo=UTC)


def _raw_connection_id(session: Session) -> int:
    """Identify the physical socket this session is talking on."""
    return id(session.connection().connection.driver_connection)


@pytest.fixture
def busy_handler() -> Any:
    """A handler that works for a while, committing as it goes, like a real discovery run."""
    previous = tasks.unregister_handler(DEMO_JOB_KIND)
    connections: set[int] = set()

    def handler(session: Session, run: JobRun) -> None:
        run.progress_total = 40
        for step in range(1, 41):
            connections.add(_raw_connection_id(session))
            session.execute(select(JobRun.id).where(JobRun.id == run.id)).all()
            tasks.checkpoint(session, run, done=step)
            time.sleep(0.01)

    tasks.register_handler(DEMO_JOB_KIND, handler)
    yield connections
    tasks.unregister_handler(DEMO_JOB_KIND)
    if previous is not None:
        tasks.register_handler(DEMO_JOB_KIND, previous)


# --- the engines ---------------------------------------------------------------------


def test_psycopg_engines_are_built_with_prepared_statements_off(db: Session) -> None:
    """Rule 3: no `_pg3_N` names exist, so no two connections can collide over one.

    Asked of the live connection rather than of the settings, because the value has to
    survive every layer between `create_engine` and psycopg to be worth anything.
    """
    assert connect_args_for("postgresql+psycopg://radar:radar@db:5432/radar") == {
        "prepare_threshold": None
    }
    assert connect_args_for("sqlite:///:memory:") == {}, "only psycopg takes the option"

    with session_scope() as app_session, scheduler_session_scope() as scheduler_session:
        for session in (app_session, scheduler_session):
            driver_connection = session.connection().connection.driver_connection
            assert driver_connection is not None
            assert driver_connection.prepare_threshold is None


def test_the_scheduler_has_its_own_engine_and_holds_no_connection_between_ticks(
    db: Session,
) -> None:
    """Rule 1. `NullPool`: there is nothing of the scheduler's for a fork to inherit."""
    assert get_scheduler_engine() is not get_engine()
    assert isinstance(get_scheduler_engine().pool, NullPool), "a pool would keep a socket alive"

    with scheduler_session_scope() as session:
        session.execute(text("SELECT 1"))


# --- the two halves running at once ----------------------------------------------------


def test_a_scheduler_thread_ticking_through_a_running_job_never_shares_its_connection(
    db: Session, fake_redis: fakeredis.FakeStrictRedis, busy_handler: set[int]
) -> None:
    """A real job and a real scheduler loop, at the same time, against real PostgreSQL.

    This is the shape that broke in production: while one job was running, the scheduler
    enqueued `scheduled:crm-sync` every minute on what turned out to be the same socket.
    """
    scheduler_connections: set[int] = set()
    tick_errors: list[BaseException] = []
    queued: list[uuid.UUID] = []

    def enqueue(job: Any) -> uuid.UUID | None:
        with scheduler_session_scope() as session:
            scheduler_connections.add(_raw_connection_id(session))
        run_id = enqueue_scheduled_run(job)
        if run_id is not None:
            queued.append(run_id)
        return run_id

    scheduler = Scheduler(fake_redis, instance_id="under-test", enqueue=enqueue)
    stop = threading.Event()

    def tick_until_stopped() -> None:
        moment = T0
        while not stop.is_set():
            try:
                scheduler.tick(moment)
            except BaseException as exc:
                tick_errors.append(exc)
            moment += timedelta(minutes=1)
            time.sleep(0.01)

    run = enqueue_run(db, search_job_id=None, kind=DEMO_JOB_KIND)
    thread = threading.Thread(target=tick_until_stopped, name="scheduler-under-test")
    thread.start()
    try:
        assert tasks.execute_job_run(run.id) is JobRunStatus.done
    finally:
        stop.set()
        thread.join(timeout=10)

    assert tick_errors == [], f"the scheduler failed while a job ran: {tick_errors[:1]}"
    assert queued, "the scheduler queued nothing, so this proved nothing"
    assert busy_handler, "the handler never ran"
    assert not (busy_handler & scheduler_connections), (
        "the job and the scheduler used the same physical connection"
    )

    db.expire_all()
    finished = db.get(JobRun, run.id)
    assert finished is not None and finished.status is JobRunStatus.done
    assert finished.progress_done == 40
    assert (
        db.scalars(
            select(JobRun.id).where(JobRun.kind == f"{SCHEDULED_KIND_PREFIX}{JOB_CRM_SYNC}")
        ).first()
        is not None
    )


def test_a_forked_job_drops_the_connections_it_inherited_from_its_parent(
    db: Session, fake_redis: fakeredis.FakeStrictRedis, busy_handler: set[int]
) -> None:
    """Rule 2, reproduced the way RQ does it: `os.fork()` with an idle pooled connection.

    The parent puts a connection back in the pool (what the scheduler thread does at the
    end of every tick) and then forks. Before the fix the child checked that same
    connection out and ran a whole job on it while the parent kept querying on it too.
    """
    run = enqueue_run(db, search_job_id=None, kind=DEMO_JOB_KIND)
    db.commit()

    # An idle connection in the shared pool: precisely what a fork copies.
    with session_scope() as session:
        session.execute(text("SELECT 1"))
    pool = get_engine().pool
    assert isinstance(pool, QueuePool) and pool.checkedin() >= 1

    started_read, started_write = os.pipe()
    pid = os.fork()
    if pid == 0:  # the work horse
        code = 1
        try:
            os.close(started_read)
            os.write(started_write, b"x")
            os.close(started_write)
            code = 0 if tasks.execute_job_run(run.id) is JobRunStatus.done else 1
        except BaseException:
            code = 2
        finally:
            os._exit(code)

    os.close(started_write)
    assert os.read(started_read, 1) == b"x"
    os.close(started_read)

    # Keep querying on the shared pool for as long as the child works: before the fix
    # this is where "prepared statement _pg3_0 already exists" surfaced.
    parent_errors: list[BaseException] = []
    status = 0
    while True:
        finished, status = os.waitpid(pid, os.WNOHANG)
        if finished:
            break
        try:
            with session_scope() as session:
                session.execute(select(JobRun.id)).all()
        except BaseException as exc:
            parent_errors.append(exc)
        time.sleep(0.01)

    assert parent_errors == [], f"the parent's connection was corrupted: {parent_errors[:1]}"
    assert os.waitstatus_to_exitcode(status) == 0, "the forked job did not finish cleanly"

    db.expire_all()
    done = db.get(JobRun, run.id)
    assert done is not None and done.status is JobRunStatus.done
    assert done.progress_done == 40
