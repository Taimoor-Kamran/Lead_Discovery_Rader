"""The run time limit is raised as `RunTimedOut`, which no `except Exception` can swallow."""

import time

import pytest
from rq.timeouts import HorseMonitorTimeoutException, JobTimeoutException

from app.workers.timeouts import RadarWorker, RunDeathPenalty, RunTimedOut


def test_a_real_alarm_raises_run_timed_out_straight_through_an_except_exception() -> None:
    def audit_loop_body() -> str:
        try:
            time.sleep(5)
        except Exception:  # what the audit loop's per-business handler does
            return "swallowed, and recorded as one business's failed audit"
        return "finished"

    started = time.monotonic()
    with pytest.raises(RunTimedOut) as caught, RunDeathPenalty(1, JobTimeoutException):
        audit_loop_body()

    assert time.monotonic() - started < 4
    assert not isinstance(caught.value, Exception)
    assert "time limit of 1 s" in str(caught.value)


def test_the_worker_uses_it() -> None:
    assert RadarWorker.death_penalty_class is RunDeathPenalty


def test_other_timeouts_keep_the_exception_rq_asked_for() -> None:
    """The worker waits on its work horse with the same class; that is not a run's limit."""
    with (
        pytest.raises(HorseMonitorTimeoutException),
        RunDeathPenalty(1, HorseMonitorTimeoutException),
    ):
        time.sleep(5)
