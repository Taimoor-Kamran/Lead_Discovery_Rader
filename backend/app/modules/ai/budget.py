"""Cost controls: a daily budget, a per-run cap, a cost estimate and reuse by input hash.

Everything that can stop a runaway spend is here and checked *before* a call is made. The
counters live in Redis so every api and worker process shares one budget, exactly as the
Places daily cap does. When prices are not configured the estimate is `None`, the budget
cannot be measured in dollars, and the daily **call** cap becomes the guard — with a
warning in the log, so nobody mistakes "no cost recorded" for "free".
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from redis import Redis

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.modules.ai.client import Tier

logger = get_logger("app.ai.budget")

KEY_PREFIX = "ai:budget"
KEY_TTL_SECONDS = 60 * 60 * 48
MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class Prices:
    input_per_million: Decimal
    output_per_million: Decimal


def prices_for(settings: Settings, tier: Tier) -> Prices | None:
    """The configured prices for a tier, or `None` when either half is missing."""
    if tier == "triage":
        raw_in, raw_out = settings.ai_triage_price_in_per_m, settings.ai_triage_price_out_per_m
    else:
        raw_in = settings.ai_escalation_price_in_per_m
        raw_out = settings.ai_escalation_price_out_per_m
    if raw_in is None or raw_out is None:
        return None
    return Prices(Decimal(str(raw_in)), Decimal(str(raw_out)))


def estimate_cost(
    settings: Settings, tier: Tier, *, tokens_in: int, tokens_out: int
) -> Decimal | None:
    """Tokens times the configured per-million price. `None` when prices are not configured."""
    prices = prices_for(settings, tier)
    if prices is None:
        return None
    cost = (Decimal(tokens_in) * prices.input_per_million) / MILLION
    cost += (Decimal(tokens_out) * prices.output_per_million) / MILLION
    return cost.quantize(COST_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class BudgetStatus:
    day: date
    calls: int
    spent_usd: Decimal
    budget_usd: Decimal
    call_cap: int

    @property
    def remaining_usd(self) -> Decimal:
        return max(self.budget_usd - self.spent_usd, Decimal(0))


class AIBudget:
    """The shared daily counters plus the per-run cap the caller keeps count of."""

    def __init__(
        self,
        redis: Redis,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._settings = settings or get_settings()
        self._clock = clock
        self._warned_no_prices = False

    # --- keys -------------------------------------------------------------------------

    def today(self) -> date:
        return datetime.fromtimestamp(self._clock(), tz=UTC).date()

    def _key(self, kind: str, day: date | None = None) -> str:
        return f"{KEY_PREFIX}:{(day or self.today()).isoformat()}:{kind}"

    # --- reads ------------------------------------------------------------------------

    def calls(self, day: date | None = None) -> int:
        raw = self._redis.get(self._key("calls", day))
        return int(raw) if isinstance(raw, bytes | str) else 0

    def spent(self, day: date | None = None) -> Decimal:
        raw = self._redis.get(self._key("usd", day))
        if not isinstance(raw, bytes | str):
            return Decimal(0)
        text = raw.decode() if isinstance(raw, bytes) else raw
        return Decimal(text).quantize(COST_PLACES)

    def status(self, day: date | None = None) -> BudgetStatus:
        return BudgetStatus(
            day=day or self.today(),
            calls=self.calls(day),
            spent_usd=self.spent(day),
            budget_usd=Decimal(str(self._settings.ai_daily_budget_usd)),
            call_cap=self._settings.ai_daily_call_cap,
        )

    @property
    def prices_configured(self) -> bool:
        return prices_for(self._settings, "triage") is not None

    # --- the guard --------------------------------------------------------------------

    def check(self, *, run_calls: int) -> BudgetDecision:
        """Whether one more call may be made. Checked before every call, never after."""
        if run_calls >= self._settings.ai_max_calls_per_run:
            return BudgetDecision(
                False, f"per-run cap of {self._settings.ai_max_calls_per_run} calls reached"
            )
        if self.calls() >= self._settings.ai_daily_call_cap:
            return BudgetDecision(
                False, f"daily call cap of {self._settings.ai_daily_call_cap} reached"
            )
        if not self.prices_configured:
            if not self._warned_no_prices:
                self._warned_no_prices = True
                logger.warning(
                    "AI prices are not configured; the daily budget is enforced by call "
                    "count only and costs are recorded as null"
                )
            return BudgetDecision(True)
        budget = Decimal(str(self._settings.ai_daily_budget_usd))
        if self.spent() >= budget:
            return BudgetDecision(False, f"daily budget of ${budget} reached")
        return BudgetDecision(True)

    # --- recording --------------------------------------------------------------------

    def record(self, cost_usd: Decimal | None) -> None:
        """Count one call that was made, and what it is estimated to have cost."""
        calls_key = self._key("calls")
        usd_key = self._key("usd")
        with self._redis.pipeline() as pipe:
            pipe.incr(calls_key)
            pipe.expire(calls_key, KEY_TTL_SECONDS)
            if cost_usd is not None and cost_usd > 0:
                pipe.incrbyfloat(usd_key, float(cost_usd))
                pipe.expire(usd_key, KEY_TTL_SECONDS)
            cast(list[object], pipe.execute())
