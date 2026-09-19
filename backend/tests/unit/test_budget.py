"""The daily budget, the per-run cap, the cost estimate and the reuse hash."""

from decimal import Decimal

import fakeredis

from app.core.config import Settings
from app.modules.ai.budget import AIBudget, estimate_cost
from tests.conftest import FakeClock


def priced(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ai_triage_price_in_per_m": 0.15,
        "ai_triage_price_out_per_m": 0.60,
        "ai_escalation_price_in_per_m": 2.50,
        "ai_escalation_price_out_per_m": 10.00,
        "ai_daily_budget_usd": 1.0,
        "ai_max_calls_per_run": 3,
        "ai_daily_call_cap": 5,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_the_cost_estimate_is_tokens_times_the_configured_price() -> None:
    settings = priced()

    triage = estimate_cost(settings, "triage", tokens_in=1_000_000, tokens_out=100_000)
    escalation = estimate_cost(settings, "escalation", tokens_in=2_000, tokens_out=500)

    assert triage == Decimal("0.210000")
    assert escalation == Decimal("0.010000")


def test_without_prices_the_estimate_is_none() -> None:
    settings = Settings(ai_triage_price_in_per_m=0.15)  # only half of the pair

    assert estimate_cost(settings, "triage", tokens_in=10, tokens_out=10) is None
    assert estimate_cost(settings, "escalation", tokens_in=10, tokens_out=10) is None


def test_the_daily_budget_stops_calls_once_spent(clock: FakeClock) -> None:
    budget = AIBudget(fakeredis.FakeStrictRedis(), settings=priced(), clock=clock)

    assert budget.check(run_calls=0).allowed is True
    budget.record(Decimal("0.60"))
    assert budget.check(run_calls=0).allowed is True
    budget.record(Decimal("0.40"))

    decision = budget.check(run_calls=0)
    assert decision.allowed is False
    assert "daily budget" in (decision.reason or "")
    assert budget.status().spent_usd == Decimal("1.000000")
    assert budget.status().remaining_usd == Decimal(0)
    assert budget.status().calls == 2


def test_the_budget_resets_at_utc_midnight(clock: FakeClock) -> None:
    budget = AIBudget(fakeredis.FakeStrictRedis(), settings=priced(), clock=clock)
    budget.record(Decimal("1.00"))
    assert budget.check(run_calls=0).allowed is False

    clock.sleep(60 * 60 * 24)

    assert budget.check(run_calls=0).allowed is True
    assert budget.spent() == Decimal(0)


def test_the_per_run_cap_is_checked_first() -> None:
    budget = AIBudget(fakeredis.FakeStrictRedis(), settings=priced(ai_max_calls_per_run=2))

    assert budget.check(run_calls=1).allowed is True
    decision = budget.check(run_calls=2)
    assert decision.allowed is False
    assert "per-run cap" in (decision.reason or "")


def test_without_prices_the_daily_call_cap_is_the_guard() -> None:
    settings = Settings(ai_daily_call_cap=2, ai_max_calls_per_run=100)
    budget = AIBudget(fakeredis.FakeStrictRedis(), settings=settings)

    assert budget.prices_configured is False
    budget.record(None)
    budget.record(None)

    decision = budget.check(run_calls=0)
    assert decision.allowed is False
    assert "daily call cap" in (decision.reason or "")
    assert budget.spent() == Decimal(0), "nothing priced, nothing spent"


def test_counters_are_shared_between_instances_on_the_same_redis() -> None:
    redis = fakeredis.FakeStrictRedis()
    first = AIBudget(redis, settings=priced())
    second = AIBudget(redis, settings=priced())

    first.record(Decimal("0.25"))

    assert second.spent() == Decimal("0.250000")
    assert second.calls() == 1
