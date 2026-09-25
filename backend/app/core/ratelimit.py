"""Per-source outbound rate limiting: a Redis token bucket, a daily cap and a per-run cap.

The bucket and the daily cap live in Redis so every api and worker process shares one
budget. The token bucket smooths bursts against a provider's per-second limit; the daily
cap is a cost guard for the day as a whole. The per-run cap is the guard against a single
runaway run: without it one run can spend the whole day's cap, which is what a Places
pagination loop did three times before v0.11.0 added it.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from redis import Redis
from redis.exceptions import WatchError

from app.core.logging import get_logger
from app.modules.adapters.errors import (
    QuotaExceededError,
    RateLimitedError,
    RunCallCapExceededError,
)

logger = get_logger("app.ratelimit")

BUCKET_KEY_PREFIX = "ratelimit:bucket"
DAILY_KEY_PREFIX = "ratelimit:daily"
RUN_KEY_PREFIX = "ratelimit:run"
DAILY_KEY_TTL_SECONDS = 60 * 60 * 48
MAX_WAIT_SECONDS = 30.0


class TokenBucket:
    """A shared token bucket. `acquire()` blocks until a token is free, or gives up."""

    def __init__(
        self,
        redis: Redis,
        *,
        source: str,
        requests_per_second: float,
        burst: int,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        max_wait_seconds: float = MAX_WAIT_SECONDS,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._redis = redis
        self._key = f"{BUCKET_KEY_PREFIX}:{source}"
        self._source = source
        self._rate = requests_per_second
        self._burst = max(burst, 1)
        self._clock = clock
        self._sleeper = sleeper
        self._max_wait = max_wait_seconds

    def acquire(self) -> None:
        """Consume one token, waiting for the refill if the bucket is empty."""
        waited = 0.0
        while True:
            wait = self._take()
            if wait <= 0:
                return
            waited += wait
            if waited > self._max_wait:
                raise RateLimitedError(
                    f"Waited {waited:.1f}s for a '{self._source}' rate-limit token and gave up",
                    source=self._source,
                )
            self._sleeper(wait)

    def _take(self) -> float:
        """Take a token if one is available; otherwise return the seconds until one is."""
        now = self._clock()
        with self._redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(self._key)  # type: ignore[no-untyped-call]
                    tokens, updated = self._read(pipe.get(self._key), now)
                    tokens = min(float(self._burst), tokens + (now - updated) * self._rate)
                    if tokens >= 1.0:
                        tokens -= 1.0
                        wait = 0.0
                    else:
                        wait = (1.0 - tokens) / self._rate
                    pipe.multi()
                    pipe.set(self._key, f"{tokens:.6f}:{now:.6f}", ex=DAILY_KEY_TTL_SECONDS)
                    pipe.execute()
                    return wait
                except WatchError:  # another process moved first; re-read and retry
                    continue

    def _read(self, raw: object, now: float) -> tuple[float, float]:
        if not isinstance(raw, bytes | str):
            return float(self._burst), now
        text = raw.decode() if isinstance(raw, bytes) else raw
        tokens_raw, _, updated_raw = text.partition(":")
        try:
            return float(tokens_raw), float(updated_raw)
        except ValueError:
            return float(self._burst), now


class DailyCallCap:
    """A per-source, per-UTC-day counter of outbound calls."""

    def __init__(
        self,
        redis: Redis,
        *,
        source: str,
        cap: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._redis = redis
        self._source = source
        self._cap = cap
        self._clock = clock

    @property
    def key(self) -> str:
        return f"{DAILY_KEY_PREFIX}:{self._source}:{self._clock().date().isoformat()}"

    def used(self) -> int:
        raw = self._redis.get(self.key)
        return int(raw) if isinstance(raw, bytes | str) else 0

    def reserve(self) -> int:
        """Claim one call against today's cap, or raise before anything is sent."""
        key = self.key
        used = int(cast(int, self._redis.incr(key)))
        if used == 1:
            self._redis.expire(key, DAILY_KEY_TTL_SECONDS)
        if used > self._cap:
            self._redis.decr(key)
            logger.warning(
                "daily call cap reached", extra={"source": self._source, "cap": self._cap}
            )
            raise QuotaExceededError(
                f"The daily call cap for '{self._source}' ({self._cap}) has been reached; "
                "no request was made. Raise the cap in the environment or wait for UTC midnight.",
                details={"source": self._source, "cap": self._cap},
                source=self._source,
            )
        return used

    def release(self) -> None:
        """Hand back a slot `reserve()` claimed for a request that was never sent."""
        self._redis.decr(self.key)


class RunCallCap:
    """A ceiling on the calls one job run may make to one source.

    Counted in Redis under the run's id, so a worker retry of the same run — which builds a
    fresh client — keeps spending from the same ceiling rather than starting a new one.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        source: str,
        cap: int,
        job_run_id: object,
        setting: str = "the per-run ceiling",
    ) -> None:
        self._redis = redis
        self._source = source
        self._cap = cap
        self._job_run_id = job_run_id
        self._setting = setting

    @property
    def key(self) -> str:
        return f"{RUN_KEY_PREFIX}:{self._source}:{self._job_run_id}"

    def used(self) -> int:
        raw = self._redis.get(self.key)
        return int(raw) if isinstance(raw, bytes | str) else 0

    def reserve(self) -> int:
        """Claim one call for this run, or raise before anything is sent."""
        key = self.key
        used = int(cast(int, self._redis.incr(key)))
        if used == 1:
            self._redis.expire(key, DAILY_KEY_TTL_SECONDS)
        if used > self._cap:
            self._redis.decr(key)
            made = used - 1
            logger.error(
                "per-run call cap reached",
                extra={
                    "source": self._source,
                    "cap": self._cap,
                    "calls_made": made,
                    "job_run_id": str(self._job_run_id),
                },
            )
            raise RunCallCapExceededError(
                f"Safety limit reached: this run made {made} '{self._source}' call(s), the "
                f"most one run of this size may make ({self._cap}), and call {made + 1} was "
                "not sent. This is not a quota and not a busy day: a normal run stays well "
                "under the limit, so reaching it means this run was looping. Find out why "
                f"before raising {self._setting}.",
                details={"source": self._source, "cap": self._cap, "calls_made": made},
                source=self._source,
            )
        return used

    def release(self) -> None:
        """Hand back a slot `reserve()` claimed for a request that was never sent."""
        self._redis.decr(self.key)


class SourceLimiter:
    """The guards a request must pass, in order: per-run cap, daily cap, token bucket.

    Each claims its slot before the next is asked, so a later guard that refuses — the
    bucket giving up after its wait — hands back what the earlier ones claimed. A request
    that was never sent must not count against a cap.
    """

    def __init__(
        self,
        bucket: TokenBucket,
        daily_cap: DailyCallCap,
        run_cap: RunCallCap | None = None,
    ) -> None:
        self._bucket = bucket
        self._daily_cap = daily_cap
        self._run_cap = run_cap

    def acquire(self) -> None:
        if self._run_cap is not None:
            self._run_cap.reserve()
        try:
            self._daily_cap.reserve()
        except BaseException:
            if self._run_cap is not None:
                self._run_cap.release()
            raise
        try:
            self._bucket.acquire()
        except BaseException:
            self._daily_cap.release()
            if self._run_cap is not None:
                self._run_cap.release()
            raise


def build_limiter(
    redis: Redis,
    *,
    source: str,
    requests_per_second: float,
    burst: int,
    daily_call_cap: int,
    run_call_cap: int | None = None,
    run_call_cap_setting: str = "the per-run ceiling",
    job_run_id: object = None,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> SourceLimiter:
    bucket = TokenBucket(
        redis,
        source=source,
        requests_per_second=requests_per_second,
        burst=burst,
        clock=clock,
        sleeper=sleeper,
    )
    daily = DailyCallCap(
        redis,
        source=source,
        cap=daily_call_cap,
        clock=lambda: datetime.fromtimestamp(clock(), tz=UTC),
    )
    run = (
        RunCallCap(
            redis,
            source=source,
            cap=run_call_cap,
            job_run_id=job_run_id,
            setting=run_call_cap_setting,
        )
        if run_call_cap is not None and job_run_id is not None
        else None
    )
    return SourceLimiter(bucket, daily, run)
