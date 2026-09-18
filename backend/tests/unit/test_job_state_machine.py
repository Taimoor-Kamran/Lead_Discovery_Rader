"""The job-run state machine, independent of the database."""

import pytest

from app.modules.jobs.models import JobRunStatus
from app.modules.jobs.state import ALLOWED_TRANSITIONS, backoff_seconds, can_transition

TERMINAL = [JobRunStatus.done, JobRunStatus.failed, JobRunStatus.cancelled]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobRunStatus.queued, JobRunStatus.running),
        (JobRunStatus.queued, JobRunStatus.cancelled),
        (JobRunStatus.running, JobRunStatus.done),
        (JobRunStatus.running, JobRunStatus.failed),
        (JobRunStatus.running, JobRunStatus.cancelled),
        (JobRunStatus.running, JobRunStatus.queued),
    ],
)
def test_allowed_transitions(current: JobRunStatus, target: JobRunStatus) -> None:
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobRunStatus.queued, JobRunStatus.done),
        (JobRunStatus.queued, JobRunStatus.failed),
        (JobRunStatus.running, JobRunStatus.running),
        (JobRunStatus.done, JobRunStatus.running),
        (JobRunStatus.failed, JobRunStatus.queued),
        (JobRunStatus.cancelled, JobRunStatus.running),
    ],
)
def test_rejected_transitions(current: JobRunStatus, target: JobRunStatus) -> None:
    assert not can_transition(current, target)


@pytest.mark.parametrize("status", TERMINAL)
def test_terminal_statuses_have_no_exit(status: JobRunStatus) -> None:
    assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_every_status_is_covered() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(JobRunStatus)


def test_backoff_grows_exponentially() -> None:
    assert [backoff_seconds(n, 2) for n in (1, 2, 3)] == [2, 4, 8]
