"""The PageSpeed client: what it reads, and how it fails without taking the audit with it."""

import json
import os
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.http import ApiHttpClient
from app.modules.audit_web.psi import (
    PSI_ENDPOINT,
    PSI_MAX_ATTEMPTS,
    PSI_TIMEOUT,
    FixturePageSpeedClient,
    NetworkPageSpeedClient,
    PageSpeedUnavailableError,
    PsiResult,
    build_psi_client,
    pagespeed_source_config,
    parse_psi,
)
from tests.conftest import FakeClock

URL = "https://example.test/"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pagespeed"


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


def lighthouse(
    score: float = 0.92,
    *,
    lcp: float = 2100.0,
    cls: float = 0.05,
    tbt: float = 180.0,
    crux: str | None = "FAST",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "lighthouseResult": {
            "categories": {"performance": {"score": score}},
            "audits": {
                "largest-contentful-paint": {"numericValue": lcp},
                "cumulative-layout-shift": {"numericValue": cls},
                "total-blocking-time": {"numericValue": tbt},
            },
        }
    }
    if crux is not None:
        payload["loadingExperience"] = {"overall_category": crux}
    return payload


# --- parsing ---------------------------------------------------------------------------


def test_a_full_response_is_read_into_the_fields_we_keep() -> None:
    result = parse_psi(lighthouse())

    assert result == PsiResult(
        performance_score=92, lcp_ms=2100, cls=0.05, tbt_ms=180, crux_category="FAST"
    )


def test_the_score_is_rounded_to_a_whole_number_out_of_a_hundred() -> None:
    assert parse_psi(lighthouse(score=0.314)).performance_score == 31


def test_missing_audits_stay_null_rather_than_zero() -> None:
    result = parse_psi({"lighthouseResult": {"categories": {"performance": {"score": 0.5}}}})

    assert result.performance_score == 50
    assert result.lcp_ms is None
    assert result.cls is None
    assert result.tbt_ms is None
    assert result.crux_category is None


def test_a_response_without_field_data_has_no_crux_category() -> None:
    assert parse_psi(lighthouse(crux=None)).crux_category is None


def test_an_error_envelope_is_not_a_result() -> None:
    with pytest.raises(PageSpeedUnavailableError) as exc:
        parse_psi({"error": {"code": 429, "message": "Quota exceeded for quota metric"}})

    assert "Quota exceeded" in exc.value.reason


def test_a_body_that_is_not_an_object_is_not_a_result() -> None:
    with pytest.raises(PageSpeedUnavailableError):
        parse_psi(["not", "an", "object"])


# --- the network client ----------------------------------------------------------------


def client(
    mock_http: respx.MockRouter, *, api_key: str = "psi-test-key", max_attempts: int = 1
) -> NetworkPageSpeedClient:
    clock = FakeClock()
    return NetworkPageSpeedClient(
        ApiHttpClient(
            source="pagespeed_insights",
            max_attempts=max_attempts,
            sleeper=clock.sleep,
            secrets=[api_key] if api_key else [],
        ),
        api_key=api_key,
    )


def test_the_client_asks_for_the_mobile_strategy(mock_http: respx.MockRouter) -> None:
    route = mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(200, json=lighthouse())
    )

    result = client(mock_http).analyse(URL)

    assert result.performance_score == 92
    request_url = str(route.calls[0].request.url)
    assert "strategy=mobile" in request_url
    assert "url=https%3A%2F%2Fexample.test%2F" in request_url


def test_the_key_travels_in_a_header_and_never_in_the_url(mock_http: respx.MockRouter) -> None:
    """Until v0.11.0 the key went as `&key=`, and httpx logged the URL with it in."""
    sentinel = "psi-key-SENTINEL-do-not-log"
    route = mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(200, json=lighthouse())
    )

    client(mock_http, api_key=sentinel).analyse(URL)

    request = route.calls[0].request
    assert request.headers["X-Goog-Api-Key"] == sentinel
    assert sentinel not in str(request.url)
    assert "key=" not in request.url.query.decode()


def test_the_key_never_reaches_an_error(mock_http: respx.MockRouter) -> None:
    sentinel = "psi-key-SENTINEL-do-not-log"
    mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(400, json={"error": {"message": f"bad key {sentinel}"}})
    )

    with pytest.raises(PageSpeedUnavailableError) as exc:
        client(mock_http, api_key=sentinel).analyse(URL)

    assert sentinel not in exc.value.reason


def test_the_production_client_waits_sixty_seconds_and_tries_twice(
    mock_http: respx.MockRouter,
) -> None:
    """A Lighthouse run takes 15-50 s; a 20 s read timeout cut 4 of 9 live calls short."""
    route = mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        side_effect=httpx.ReadTimeout("no answer yet")
    )
    clock = FakeClock()
    psi = build_psi_client(
        settings=_settings(pagespeed_api_key=SecretStr("psi-test-key")),
        redis_client=fakeredis.FakeStrictRedis(),
        clock=clock,
        sleeper=clock.sleep,
        meter=lambda call: None,
    )

    with pytest.raises(PageSpeedUnavailableError) as exc:
        psi.analyse("https://www.real-host-with-no-demo-fixture.com/")

    assert route.call_count == PSI_MAX_ATTEMPTS == 2
    assert all(
        call.request.extensions["timeout"]["read"] == PSI_TIMEOUT.read == 60.0
        for call in route.calls
    ), "the timeout httpx actually applied to each attempt"
    assert "TransientError" in exc.value.reason


def test_a_quota_error_is_reported_as_unavailable_rather_than_raised(
    mock_http: respx.MockRouter,
) -> None:
    mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(429, json={"error": {"message": "Quota exceeded"}})
    )

    with pytest.raises(PageSpeedUnavailableError) as exc:
        client(mock_http).analyse(URL)

    assert "RateLimitedError" in exc.value.reason


def test_a_server_error_is_reported_as_unavailable(mock_http: respx.MockRouter) -> None:
    mock_http.get(url__startswith=PSI_ENDPOINT).mock(return_value=httpx.Response(503))

    with pytest.raises(PageSpeedUnavailableError):
        client(mock_http).analyse(URL)


def test_a_daily_cap_that_is_already_spent_stops_the_call(mock_http: respx.MockRouter) -> None:
    """The limiter raises before anything leaves the process; PSI is simply unavailable."""
    route = mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(200, json=lighthouse())
    )
    redis_client = fakeredis.FakeStrictRedis()
    clock = FakeClock()
    psi = build_psi_client(
        settings=_settings(psi_daily_call_cap=0, pagespeed_api_key=SecretStr("psi-test-key")),
        redis_client=redis_client,
        clock=clock,
        sleeper=clock.sleep,
        meter=lambda call: None,
    )

    with pytest.raises(PageSpeedUnavailableError) as exc:
        psi.analyse(URL)

    assert "QuotaExceededError" in exc.value.reason
    assert route.call_count == 0


def _settings(**overrides: Any) -> Any:
    from app.core.config import Settings

    return Settings(jwt_secret=SecretStr("x" * 32), environment="ci", **overrides)


# --- the fixture client ----------------------------------------------------------------


def test_the_fixture_client_reads_a_demo_sites_psi_file(tmp_path: Any) -> None:
    site = tmp_path / "demo.invalid"
    site.mkdir()
    (site / "psi.json").write_text(json.dumps(lighthouse(score=0.31)), encoding="utf-8")

    result = FixturePageSpeedClient(root=str(tmp_path)).analyse("https://demo.invalid/")

    assert result.performance_score == 31


def test_the_fixture_client_reports_a_missing_file_as_unavailable(tmp_path: Any) -> None:
    os.mkdir(os.path.join(tmp_path, "nopsi.invalid"))

    with pytest.raises(PageSpeedUnavailableError) as exc:
        FixturePageSpeedClient(root=str(tmp_path)).analyse("https://nopsi.invalid/")

    assert "psi.json" in exc.value.reason


def test_the_fixture_client_never_calls_out_for_a_reserved_host(tmp_path: Any) -> None:
    class Forbidden:
        def analyse(self, url: str) -> PsiResult:  # pragma: no cover - must not be reached
            raise AssertionError("a .invalid host must never reach the network client")

    with pytest.raises(PageSpeedUnavailableError):
        FixturePageSpeedClient(fallback=Forbidden(), root=str(tmp_path)).analyse(
            "https://missing.invalid/"
        )


def test_the_fixture_client_hands_an_unknown_real_host_to_the_live_client(tmp_path: Any) -> None:
    class Answering:
        def analyse(self, url: str) -> PsiResult:
            return PsiResult(performance_score=77)

    fixture = FixturePageSpeedClient(fallback=Answering(), root=str(tmp_path))

    assert fixture.analyse("https://real.example/").performance_score == 77


# --- the source row --------------------------------------------------------------------


def test_the_source_config_marks_pagespeed_as_a_service() -> None:
    config = pagespeed_source_config(_settings())

    assert config["role"] == "audit_service"
    assert config["display_name"] == "PageSpeed Insights"
    assert config["rate_limit"]["daily_call_cap"] == 200


# --- v0.11.0: accessibility and best practices ---------------------------------------


RECORDED = "runpagespeed_mobile_recorded.json"


def test_all_three_category_scores_are_read_from_a_recorded_response() -> None:
    """A live response (Lighthouse 13.5.0, example.com, 2026-09-23). A perfect score comes
    back as the integer `1`, not `1.0`, and must still read as 100."""
    payload = fixture(RECORDED)
    assert payload["lighthouseResult"]["categories"]["performance"]["score"] == 1

    result = parse_psi(payload)

    assert result.performance_score == 100
    assert result.accessibility_score == 96
    assert result.best_practices_score == 96
    assert (result.lcp_ms, result.cls, result.tbt_ms) == (757, 0.0, 0)
    assert result.crux_category == "FAST"


def test_a_category_lighthouse_could_not_score_is_null_not_zero() -> None:
    """The recorded response with one score nulled, as Lighthouse reports an unscorable one."""
    payload = fixture(RECORDED)
    payload["lighthouseResult"]["categories"]["accessibility"]["score"] = None

    result = parse_psi(payload)

    assert result.accessibility_score is None
    assert result.best_practices_score == 96


def test_a_response_that_only_scored_performance_leaves_the_others_null() -> None:
    result = parse_psi(lighthouse())

    assert result.accessibility_score is None
    assert result.best_practices_score is None


def test_the_client_asks_for_all_three_categories_in_one_request(
    mock_http: respx.MockRouter,
) -> None:
    route = mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(200, json=fixture(RECORDED))
    )

    result = client(mock_http).analyse(URL)

    assert route.call_count == 1
    query = route.calls[0].request.url.params
    assert query.get_list("category") == ["performance", "accessibility", "best-practices"]
    assert (result.accessibility_score, result.best_practices_score) == (96, 96)


def test_without_a_key_pagespeed_is_never_called(mock_http: respx.MockRouter) -> None:
    route = mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(200, json=lighthouse())
    )

    with pytest.raises(PageSpeedUnavailableError) as exc:
        client(mock_http, api_key="").analyse(URL)

    assert "PAGESPEED_API_KEY" in exc.value.reason
    assert route.call_count == 0
