"""The sync loop: ticks until stopped and outlives a failing tick."""

import threading

from app.modules.crm.worker import run_sync_loop


def test_the_loop_ticks_until_stopped_and_a_failing_tick_does_not_end_it() -> None:
    stop = threading.Event()
    calls: list[int] = []

    def tick() -> int:
        calls.append(len(calls))
        if len(calls) == 2:
            raise RuntimeError("the database blinked")
        if len(calls) >= 4:
            stop.set()
        return 1

    waits: list[float] = []
    ticks = run_sync_loop(stop, interval_seconds=60, tick=tick, sleeper=waits.append)

    assert ticks == 4
    assert calls == [0, 1, 2, 3]
    assert waits == [60.0, 60.0, 60.0, 60.0]
