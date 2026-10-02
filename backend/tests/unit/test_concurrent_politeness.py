"""v0.15.0: the per-host gap and the call caps hold when several threads claim at once.

These run real threads against one in-process fakeredis server, which serialises commands
the way a Redis server does. The throttle test uses the real clock with a short interval:
what is asserted is an ordering fact — no two claims on one host closer than the interval
— so a slow machine makes it take longer, never pass wrongly.
"""

import threading
import time
from collections.abc import Callable
from itertools import pairwise

import fakeredis
import pytest

from app.core.ratelimit import DailyCallCap, RunCallCap
from app.core.safe_fetch import HostThrottle
from app.modules.adapters.errors import QuotaExceededError, RunCallCapExceededError

INTERVAL = 0.2
THREADS = 8


def together(count: int, work: Callable[[int], object]) -> None:
    """Start `count` threads behind one barrier so they all claim at the same moment."""
    barrier = threading.Barrier(count)
    errors: list[BaseException] = []

    def run(index: int) -> None:
        barrier.wait()
        try:
            work(index)
        except BaseException as exc:  # handed back to the test, not lost in a thread
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a thread never finished"
    if errors:
        raise errors[0]


def test_one_host_is_never_claimed_twice_within_the_interval_by_concurrent_threads() -> None:
    redis = fakeredis.FakeStrictRedis()
    throttle = HostThrottle(redis, interval_seconds=INTERVAL)
    claimed: list[float] = []
    lock = threading.Lock()

    def claim(_: int) -> None:
        throttle.wait("shared.test")
        with lock:
            claimed.append(time.monotonic())

    together(THREADS, claim)

    claimed.sort()
    gaps = [later - earlier for earlier, later in pairwise(claimed)]
    assert len(claimed) == THREADS
    # A hair under the interval: the key's life is measured by Redis's clock and the gap
    # here by `monotonic` after the claim returns, so a millisecond of scheduling separates
    # them. The old read-then-write claim let threads through microseconds apart.
    assert min(gaps) >= INTERVAL - 0.01, f"two claims {min(gaps):.4f}s apart"


def test_different_hosts_do_not_wait_for_each_other() -> None:
    redis = fakeredis.FakeStrictRedis()
    throttle = HostThrottle(redis, interval_seconds=5.0)
    waits: list[float] = []

    together(THREADS, lambda index: waits.append(throttle.wait(f"host-{index}.test")))

    assert waits == [0.0] * THREADS


@pytest.mark.parametrize("left", [3, 0, 1])
def test_the_daily_cap_admits_exactly_what_is_left_when_more_threads_claim(left: int) -> None:
    redis = fakeredis.FakeStrictRedis()
    cap = DailyCallCap(redis, source="pagespeed", cap=10)
    for _ in range(10 - left):
        cap.reserve()
    admitted: list[int] = []
    refused: list[int] = []

    def claim(index: int) -> None:
        try:
            cap.reserve()
            admitted.append(index)
        except QuotaExceededError:
            refused.append(index)

    together(THREADS, claim)

    assert len(admitted) == left
    assert len(refused) == THREADS - left, "the rest raise before anything is sent"
    assert cap.used() == 10, "never over the cap, and refusals hand their claim back"


def test_the_per_run_cap_admits_exactly_what_is_left_when_more_threads_claim() -> None:
    redis = fakeredis.FakeStrictRedis()
    cap = RunCallCap(redis, source="google_places", cap=5, job_run_id="run-1")
    for _ in range(2):
        cap.reserve()
    admitted: list[int] = []
    refused: list[int] = []

    def claim(index: int) -> None:
        try:
            cap.reserve()
            admitted.append(index)
        except RunCallCapExceededError:
            refused.append(index)

    together(THREADS, claim)

    assert (len(admitted), len(refused)) == (3, THREADS - 3)
    assert cap.used() == 5


def test_several_businesses_on_one_host_fetched_through_one_shared_fetcher_keep_the_gap() -> None:
    """The pool shares one `SafeFetcher`; its page requests to one host keep the interval."""
    from app.core.config import Settings
    from app.core.fetch_backends import BackendResponse, FetchRequest
    from app.core.safe_fetch import SafeFetcher

    sent: list[tuple[str, float]] = []
    lock = threading.Lock()

    class TimingBackend:
        resolves_dns = True

        def handles(self, host: str) -> bool:
            return True

        def get(self, request: FetchRequest) -> BackendResponse:
            with lock:
                sent.append((request.parts.hostname or "", time.monotonic()))
            return BackendResponse(
                status_code=200, headers={"content-type": "text/html"}, body=b"<p>hi</p>"
            )

    fetcher = SafeFetcher(
        redis=fakeredis.FakeStrictRedis(),
        backends=[TimingBackend()],
        settings=Settings(
            jwt_secret="x" * 32,  # type: ignore[arg-type]
            bot_contact="https://agency.example/bot",
            audit_host_throttle_seconds=INTERVAL,
        ),
        resolver=lambda host, port: ["93.184.216.34"],
    )
    # Six businesses: four share one host, two have their own.
    sites = ["shared.test"] * 4 + ["own-a.test", "own-b.test"]

    together(len(sites), lambda index: fetcher.fetch(f"https://{sites[index]}/"))

    shared = sorted(at for host, at in sent if host == "shared.test")
    assert len(shared) == 4
    gaps = [later - earlier for earlier, later in pairwise(shared)]
    assert min(gaps) >= INTERVAL - 0.01
    assert {host for host, _ in sent} == {"shared.test", "own-a.test", "own-b.test"}
