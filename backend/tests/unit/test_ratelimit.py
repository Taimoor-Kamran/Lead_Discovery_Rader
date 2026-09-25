"""The Redis token bucket and the daily call cap.

The bucket smooths bursts against a provider's per-second limit; the cap is the cost
guard that stops a runaway job from spending real money. Both live in Redis so every api
and worker process shares one budget.
"""

from datetime import UTC, datetime
from typing import cast

import fakeredis
import pytest

from app.core.ratelimit import (
    DailyCallCap,
    RunCallCap,
    SourceLimiter,
    TokenBucket,
    build_limiter,
)
from app.modules.adapters.errors import (
    QuotaExceededError,
    RateLimitedError,
    RunCallCapExceededError,
)
from tests.conftest import FakeClock


def make_bucket(
    redis: fakeredis.FakeStrictRedis,
    clock: FakeClock,
    *,
    rate: float = 5.0,
    burst: int = 5,
    max_wait_seconds: float = 30.0,
) -> TokenBucket:
    return TokenBucket(
        redis,
        source="example",
        requests_per_second=rate,
        burst=burst,
        clock=clock,
        sleeper=clock.sleep,
        max_wait_seconds=max_wait_seconds,
    )


def test_a_full_bucket_lets_the_burst_through_without_waiting(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    bucket = make_bucket(fake_redis, clock, rate=5.0, burst=5)

    for _ in range(5):
        bucket.acquire()

    assert clock.delays == []


def test_the_next_call_after_the_burst_waits_for_one_refill(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    bucket = make_bucket(fake_redis, clock, rate=5.0, burst=5)
    for _ in range(5):
        bucket.acquire()

    bucket.acquire()

    assert clock.delays == [pytest.approx(0.2)]


def test_tokens_refill_as_time_passes(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    bucket = make_bucket(fake_redis, clock, rate=2.0, burst=2)
    bucket.acquire()
    bucket.acquire()

    clock.now += 10  # ten idle seconds is far more than the bucket can hold
    bucket.acquire()
    bucket.acquire()

    assert clock.delays == [], "a refilled bucket never waits"


def test_the_bucket_is_shared_between_clients_of_the_same_source(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    first = make_bucket(fake_redis, clock, rate=1.0, burst=1)
    second = make_bucket(fake_redis, clock, rate=1.0, burst=1)

    first.acquire()
    second.acquire()

    assert clock.delays == [pytest.approx(1.0)], "the second client saw the first one's spend"


def test_two_sources_have_separate_buckets(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    one = TokenBucket(
        fake_redis,
        source="one",
        requests_per_second=1.0,
        burst=1,
        clock=clock,
        sleeper=clock.sleep,
    )
    two = TokenBucket(
        fake_redis,
        source="two",
        requests_per_second=1.0,
        burst=1,
        clock=clock,
        sleeper=clock.sleep,
    )

    one.acquire()
    two.acquire()

    assert clock.delays == []


def test_waiting_far_too_long_gives_up_rather_than_hanging_the_worker(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    bucket = make_bucket(fake_redis, clock, rate=0.1, burst=1, max_wait_seconds=5.0)
    bucket.acquire()

    with pytest.raises(RateLimitedError):
        bucket.acquire()


def test_a_corrupt_bucket_value_is_treated_as_a_full_bucket(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    fake_redis.set("ratelimit:bucket:example", "not-a-bucket")
    bucket = make_bucket(fake_redis, clock, rate=1.0, burst=3)

    bucket.acquire()

    assert clock.delays == []


def test_a_zero_rate_is_rejected_rather_than_dividing_by_zero(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    with pytest.raises(ValueError, match="requests_per_second"):
        make_bucket(fake_redis, clock, rate=0.0)


# --- the daily cap ----------------------------------------------------------------


def make_cap(
    redis: fakeredis.FakeStrictRedis, cap: int, *, day: str = "2026-09-19"
) -> DailyCallCap:
    moment = datetime.fromisoformat(f"{day}T12:00:00+00:00")
    return DailyCallCap(redis, source="example", cap=cap, clock=lambda: moment)


def test_the_daily_cap_counts_up_to_the_limit(fake_redis: fakeredis.FakeStrictRedis) -> None:
    cap = make_cap(fake_redis, 3)

    assert [cap.reserve() for _ in range(3)] == [1, 2, 3]
    assert cap.used() == 3


def test_the_call_after_the_cap_is_refused(fake_redis: fakeredis.FakeStrictRedis) -> None:
    cap = make_cap(fake_redis, 2)
    cap.reserve()
    cap.reserve()

    with pytest.raises(QuotaExceededError) as caught:
        cap.reserve()

    assert "no request was made" in str(caught.value)
    assert caught.value.retryable is False
    assert cap.used() == 2, "a refused call does not inflate tomorrow's arithmetic"


def test_the_cap_resets_at_utc_midnight(fake_redis: fakeredis.FakeStrictRedis) -> None:
    make_cap(fake_redis, 1, day="2026-09-19").reserve()

    tomorrow = make_cap(fake_redis, 1, day="2026-09-20")

    assert tomorrow.used() == 0
    assert tomorrow.reserve() == 1


def test_the_counter_is_given_an_expiry_so_it_cannot_accumulate_forever(
    fake_redis: fakeredis.FakeStrictRedis,
) -> None:
    cap = make_cap(fake_redis, 5)
    cap.reserve()

    assert int(cast(int, fake_redis.ttl(cap.key))) > 0


def test_the_limiter_checks_the_cap_before_the_bucket(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    """A refused call must not first burn a token it is never going to use."""
    limiter = SourceLimiter(
        make_bucket(fake_redis, clock, rate=1.0, burst=1), make_cap(fake_redis, 0)
    )

    with pytest.raises(QuotaExceededError):
        limiter.acquire()

    assert clock.delays == []


def test_build_limiter_wires_both_guards(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    limiter = build_limiter(
        fake_redis,
        source="example",
        requests_per_second=10.0,
        burst=2,
        daily_call_cap=2,
        clock=clock,
        sleeper=clock.sleep,
    )

    limiter.acquire()
    limiter.acquire()

    with pytest.raises(QuotaExceededError):
        limiter.acquire()


def test_build_limiter_counts_the_day_from_its_own_clock(
    fake_redis: fakeredis.FakeStrictRedis,
) -> None:
    """The cap must roll over with the injected clock, not with wall-clock time."""
    clock = FakeClock(start=datetime(2026, 9, 19, 23, 59, 0, tzinfo=UTC).timestamp())
    limiter = build_limiter(
        fake_redis,
        source="example",
        requests_per_second=10.0,
        burst=5,
        daily_call_cap=1,
        clock=clock,
        sleeper=clock.sleep,
    )
    limiter.acquire()

    clock.now += 120  # over midnight

    limiter.acquire()


# --- v0.11.0: a slot is only spent on a request that is sent -------------------------------


def test_a_bucket_that_gives_up_hands_back_the_daily_slot(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    """The limiter reserves the daily slot first; a bucket timeout means nothing was sent."""
    cap = make_cap(fake_redis, 10)
    limiter = SourceLimiter(
        make_bucket(fake_redis, clock, rate=0.1, burst=1, max_wait_seconds=5.0), cap
    )
    limiter.acquire()
    assert cap.used() == 1

    with pytest.raises(RateLimitedError):
        limiter.acquire()

    assert cap.used() == 1, "the call that timed out waiting was never made, so never counted"


def test_a_bucket_that_gives_up_hands_back_the_run_slot_too(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    run = make_run_cap(fake_redis, 10)
    limiter = SourceLimiter(
        make_bucket(fake_redis, clock, rate=0.1, burst=1, max_wait_seconds=5.0),
        make_cap(fake_redis, 10),
        run,
    )
    limiter.acquire()

    with pytest.raises(RateLimitedError):
        limiter.acquire()

    assert run.used() == 1


def test_a_daily_refusal_hands_back_the_run_slot(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    run = make_run_cap(fake_redis, 10)
    limiter = SourceLimiter(make_bucket(fake_redis, clock), make_cap(fake_redis, 0), run)

    with pytest.raises(QuotaExceededError):
        limiter.acquire()

    assert run.used() == 0


# --- v0.11.0: the per-run safety limit ----------------------------------------------------


def make_run_cap(
    redis: fakeredis.FakeStrictRedis, cap: int, *, job_run_id: str = "run-1"
) -> RunCallCap:
    return RunCallCap(
        redis, source="example", cap=cap, job_run_id=job_run_id, setting="EXAMPLE_MULTIPLIER"
    )


def test_the_run_limit_refuses_the_call_after_it_and_says_plainly_what_it_is(
    fake_redis: fakeredis.FakeStrictRedis,
) -> None:
    run = make_run_cap(fake_redis, 3)
    assert [run.reserve() for _ in range(3)] == [1, 2, 3]

    with pytest.raises(RunCallCapExceededError) as caught:
        run.reserve()

    message = str(caught.value)
    assert message.startswith("Safety limit reached")
    assert "made 3 'example' call(s)" in message
    assert "(3)" in message and "call 4 was not sent" in message
    assert "not a quota" in message and "looping" in message
    assert "EXAMPLE_MULTIPLIER" in message
    assert caught.value.details == {"source": "example", "cap": 3, "calls_made": 3}
    assert caught.value.retryable is False, "a retry would run the same loop again"
    assert run.used() == 3, "the refused call is not counted"


def test_the_run_limit_holds_across_a_worker_retry_of_the_same_run(
    fake_redis: fakeredis.FakeStrictRedis,
) -> None:
    """A retried run builds a fresh client; it must not get a fresh limit with it."""
    make_run_cap(fake_redis, 2).reserve()
    make_run_cap(fake_redis, 2).reserve()

    with pytest.raises(RunCallCapExceededError):
        make_run_cap(fake_redis, 2).reserve()


def test_each_run_has_its_own_limit(fake_redis: fakeredis.FakeStrictRedis) -> None:
    make_run_cap(fake_redis, 1, job_run_id="run-1").reserve()

    assert make_run_cap(fake_redis, 1, job_run_id="run-2").reserve() == 1


def test_the_run_limit_is_checked_before_the_daily_cap(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    """A looping run is stopped without spending another slot of the day's budget."""
    cap = make_cap(fake_redis, 100)
    limiter = SourceLimiter(make_bucket(fake_redis, clock), cap, make_run_cap(fake_redis, 2))
    limiter.acquire()
    limiter.acquire()

    with pytest.raises(RunCallCapExceededError):
        limiter.acquire()

    assert cap.used() == 2


def test_build_limiter_applies_a_run_limit_only_for_a_known_run(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock
) -> None:
    def limiter(job_run_id: str | None) -> SourceLimiter:
        return build_limiter(
            fake_redis,
            source="example",
            requests_per_second=100.0,
            burst=100,
            daily_call_cap=100,
            run_call_cap=1,
            job_run_id=job_run_id,
            clock=clock,
            sleeper=clock.sleep,
        )

    bounded = limiter("run-1")
    bounded.acquire()
    with pytest.raises(RunCallCapExceededError):
        bounded.acquire()

    unbounded = limiter(None)
    unbounded.acquire()
    unbounded.acquire()
