"""A run's time limit, raised as something no handler can mistake for its own failure.

RQ enforces a job's timeout by raising `JobTimeoutException` from a signal handler in the
middle of whatever the job is doing. That class is an `Exception`, so any per-item
`except Exception` that keeps "one bad item from costing the run" caught it too: an audit
run killed at 180 s recorded the business it was on as a failed audit and carried on as
if nothing had happened (spec v0.11.0). `RunTimedOut` is a `BaseException`, like
`KeyboardInterrupt`, so it passes every such handler and ends the run as a timeout.
"""

from typing import Any

from rq import Worker
from rq.timeouts import JobTimeoutException, UnixSignalDeathPenalty


class RunTimedOut(BaseException):
    """The run reached the time limit it was enqueued with."""

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            f"The run reached its time limit of {int(timeout_seconds)} s and was stopped. "
            "Everything it had stored before then is kept."
        )
        self.timeout_seconds = timeout_seconds


class RunDeathPenalty(UnixSignalDeathPenalty):
    """RQ's signal-based timeout, raising `RunTimedOut` for a job's own time limit.

    The worker also uses this class for other timeouts (waiting on the work horse, job
    callbacks); those keep the exception RQ asked for.
    """

    def __init__(self, timeout: Any, exception: Any = JobTimeoutException, **kwargs: Any) -> None:
        super().__init__(timeout, exception, **kwargs)  # type: ignore[no-untyped-call]
        self._run_limit = exception is JobTimeoutException

    def handle_death_penalty(self, signum: Any, frame: Any) -> None:
        if self._run_limit:
            raise RunTimedOut(self._timeout)
        super().handle_death_penalty(signum, frame)  # type: ignore[no-untyped-call]


class RadarWorker(Worker):
    death_penalty_class = RunDeathPenalty
