"""The job framework: enqueue, run, retry with backoff, cancel, idempotency."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import DEMO_JOB_KIND, DISCOVERY_JOB_KIND, enqueue_run
from app.workers import tasks
from tests.conftest import auth_headers, geo_payload

ALWAYS_FAILS = "always-fails"
FAILS_TWICE = "fails-twice"


class RecordingSleeper:
    """Stands in for time.sleep so a backoff schedule can be asserted, not waited out."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def sleeper() -> RecordingSleeper:
    return RecordingSleeper()


@pytest.fixture(autouse=True)
def _test_handlers() -> Any:
    """Register throwaway handlers and remove them again afterwards."""

    def always_fails(session: Session, run: JobRun) -> None:
        raise RuntimeError("the source is unreachable")

    attempts: dict[uuid.UUID, int] = {}

    def fails_twice(session: Session, run: JobRun) -> None:
        attempts[run.id] = attempts.get(run.id, 0) + 1
        if attempts[run.id] < 3:
            raise RuntimeError("transient blip")
        tasks.checkpoint(session, run, done=run.progress_total)

    tasks.register_handler(ALWAYS_FAILS, always_fails)
    tasks.register_handler(FAILS_TWICE, fails_twice)
    yield
    tasks.unregister_handler(ALWAYS_FAILS)
    tasks.unregister_handler(FAILS_TWICE)


def make_search_job(client: TestClient, user: User) -> str:
    response = client.post(
        "/api/v1/search-jobs",
        json={"name": "demo", "geo": geo_payload(), "industry": "dental", "source_ids": []},
        headers=auth_headers(client, user),
    )
    assert response.status_code == 201
    job_id: str = response.json()["id"]
    return job_id


def test_a_run_goes_queued_then_running_then_done(
    client: TestClient, db: Session, sales_user: User
) -> None:
    headers = auth_headers(client, sales_user)
    run = enqueue_run(db, search_job_id=None, kind=DEMO_JOB_KIND, actor_id=sales_user.id)
    run_id = str(run.id)
    assert run.status is JobRunStatus.queued

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    status = client.get(f"/api/v1/jobs/{run_id}/status", headers=headers).json()
    assert status["status"] == JobRunStatus.done.value
    assert status["progress_done"] == status["progress_total"] > 0
    assert status["started_at"] and status["finished_at"]
    assert status["error"] is None

    db.expire_all()
    transitions = [
        row.after["status"]
        for row in db.scalars(select(AuditLog).where(AuditLog.action == "job_run.status_changed"))
        if row.after
    ]
    assert transitions == ["running", "done"]


def test_a_failing_job_is_retried_to_the_limit_then_fails(
    db: Session, sales_user: User, sleeper: RecordingSleeper
) -> None:
    settings = get_settings()
    run = enqueue_run(db, search_job_id=None, kind=ALWAYS_FAILS, actor_id=sales_user.id)

    assert tasks.execute_job_run(run.id, sleeper=sleeper) is JobRunStatus.failed

    db.expire_all()
    stored = db.get(JobRun, run.id)
    assert stored is not None
    assert stored.status is JobRunStatus.failed
    assert stored.attempts == settings.job_max_attempts == 3
    assert stored.error is not None
    assert "the source is unreachable" in stored.error
    # One sleep between each pair of attempts, growing exponentially.
    assert sleeper.delays == [2, 4]


def test_a_transient_failure_eventually_succeeds(
    db: Session, sales_user: User, sleeper: RecordingSleeper
) -> None:
    run = enqueue_run(
        db, search_job_id=None, kind=FAILS_TWICE, actor_id=sales_user.id, progress_total=4
    )

    assert tasks.execute_job_run(run.id, sleeper=sleeper) is JobRunStatus.done

    db.expire_all()
    stored = db.get(JobRun, run.id)
    assert stored is not None
    assert stored.attempts == 3
    assert stored.progress_done == 4
    assert sleeper.delays == [2, 4]


def test_cancelling_a_queued_run_cancels_it_immediately(
    client: TestClient, db: Session, sales_user: User
) -> None:
    headers = auth_headers(client, sales_user)
    job_id = make_search_job(client, sales_user)
    run_id = client.post(f"/api/v1/search-jobs/{job_id}/run", headers=headers).json()["id"]

    cancel = client.post(f"/api/v1/jobs/{run_id}/cancel", headers=headers)
    assert cancel.status_code == 200
    assert cancel.json()["cancel_requested"] is True
    assert cancel.json()["status"] == JobRunStatus.cancelled.value

    # The worker later picks the run up and leaves it alone.
    assert tasks.execute_job_run(run_id) is JobRunStatus.cancelled


def test_cancelling_stops_a_running_demo_job_before_it_finishes(
    client: TestClient, db: Session, sales_user: User
) -> None:
    """The cancel request arrives over the API while the demo job is mid-flight."""
    headers = auth_headers(client, sales_user)
    run_id = str(enqueue_run(db, search_job_id=None, kind=DEMO_JOB_KIND, actor_id=sales_user.id).id)

    cancel_at_step = 2

    def cancel_midway(session: Session, run: JobRun) -> None:
        steps = run.progress_total or tasks.DEMO_DEFAULT_STEPS
        run.progress_total = steps
        tasks.checkpoint(session, run, done=0)
        for step in range(1, steps + 1):
            if step == cancel_at_step:
                response = client.post(f"/api/v1/jobs/{run_id}/cancel", headers=headers)
                assert response.status_code == 200
                assert response.json()["status"] == JobRunStatus.running.value
            tasks.checkpoint(session, run, done=step)

    original = tasks.get_handler(DEMO_JOB_KIND)
    tasks.register_handler(DEMO_JOB_KIND, cancel_midway)
    try:
        assert tasks.execute_job_run(run_id) is JobRunStatus.cancelled
    finally:
        tasks.register_handler(DEMO_JOB_KIND, original)

    status = client.get(f"/api/v1/jobs/{run_id}/status", headers=headers).json()
    assert status["status"] == JobRunStatus.cancelled.value
    assert 0 < status["progress_done"] < status["progress_total"]
    assert status["finished_at"]


def test_cancelling_a_finished_run_leaves_it_alone(
    client: TestClient, db: Session, sales_user: User
) -> None:
    headers = auth_headers(client, sales_user)
    job_id = make_search_job(client, sales_user)
    run_id = client.post(f"/api/v1/search-jobs/{job_id}/run", headers=headers).json()["id"]
    tasks.execute_job_run(run_id)

    response = client.post(f"/api/v1/jobs/{run_id}/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == JobRunStatus.done.value


def test_the_same_idempotency_key_does_not_create_a_second_run(
    client: TestClient, db: Session, sales_user: User
) -> None:
    headers = auth_headers(client, sales_user) | {"Idempotency-Key": "run-once-please"}
    job_id = make_search_job(client, sales_user)

    first = client.post(f"/api/v1/search-jobs/{job_id}/run", headers=headers)
    second = client.post(f"/api/v1/search-jobs/{job_id}/run", headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert db.scalar(select(JobRun.id).where(JobRun.id == uuid.UUID(first.json()["id"])))
    assert len(list(db.scalars(select(JobRun)))) == 1


def test_different_idempotency_keys_create_separate_runs(
    client: TestClient, db: Session, sales_user: User
) -> None:
    headers = auth_headers(client, sales_user)
    job_id = make_search_job(client, sales_user)

    first = client.post(
        f"/api/v1/search-jobs/{job_id}/run", headers=headers | {"Idempotency-Key": "a"}
    )
    second = client.post(
        f"/api/v1/search-jobs/{job_id}/run", headers=headers | {"Idempotency-Key": "b"}
    )
    assert first.json()["id"] != second.json()["id"]
    assert len(list(db.scalars(select(JobRun)))) == 2


def test_running_an_unknown_search_job_is_404(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.post(
        "/api/v1/search-jobs/00000000-0000-0000-0000-000000000000/run",
        headers=auth_headers(client, sales_user),
    )
    assert response.status_code == 404


def test_the_run_is_placed_on_the_queue(client: TestClient, db: Session, sales_user: User) -> None:
    from app.core.redis import get_queue

    job_id = make_search_job(client, sales_user)
    run_id = client.post(
        f"/api/v1/search-jobs/{job_id}/run", headers=auth_headers(client, sales_user)
    ).json()["id"]

    queued = get_queue().get_job_ids()
    assert run_id in queued


def test_the_default_run_kind_is_discovery(
    client: TestClient, db: Session, sales_user: User
) -> None:
    job_id = make_search_job(client, sales_user)
    body = client.post(
        f"/api/v1/search-jobs/{job_id}/run", headers=auth_headers(client, sales_user)
    ).json()
    assert body["kind"] == DISCOVERY_JOB_KIND


def test_a_run_with_no_registered_handler_ends_failed(
    db: Session, sales_user: User, sleeper: RecordingSleeper
) -> None:
    run = enqueue_run(db, search_job_id=None, kind="no-such-kind", actor_id=sales_user.id)

    assert tasks.execute_job_run(run.id, sleeper=sleeper) is JobRunStatus.failed

    db.expire_all()
    stored = db.get(JobRun, run.id)
    assert stored is not None
    assert stored.error is not None
    assert "no-such-kind" in stored.error
