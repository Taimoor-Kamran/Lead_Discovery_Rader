"""The shared outbound client's failure policy (blueprint slide 44).

Every branch of the table in `app/core/http.py` gets a test here, because this is the one
place that decides whether a source failure costs a retry, a run or an operator's evening.
"""

from typing import Any

import httpx
import pytest
import respx

from app.core.http import MAX_ERROR_BODY_CHARS, ApiCallRecord, ApiHttpClient
from app.modules.adapters.errors import (
    AdapterError,
    AuthError,
    QuotaExceededError,
    RateLimitedError,
    SchemaError,
    TransientError,
)
from tests.conftest import FakeClock

URL = "https://api.example.test/v1/things:search"


class StubLimiter:
    def __init__(self, error: Exception | None = None) -> None:
        self.acquired = 0
        self.error = error

    def acquire(self) -> None:
        self.acquired += 1
        if self.error is not None:
            raise self.error


def identity(body: Any) -> Any:
    return body


def make_client(
    clock: FakeClock, *, limiter: StubLimiter | None = None, secrets: list[str] | None = None
) -> tuple[ApiHttpClient, list[ApiCallRecord]]:
    metered: list[ApiCallRecord] = []
    client = ApiHttpClient(
        source="example",
        meter=metered.append,
        limiter=limiter,
        sleeper=clock.sleep,
        jitter=lambda: 0.0,
        secrets=secrets or [],
    )
    return client, metered


def test_a_successful_call_returns_the_parsed_body_and_meters_one_attempt(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(return_value=httpx.Response(200, json={"places": []}))
    client, metered = make_client(clock)

    assert client.request_json("POST", URL, parse=identity, json={"q": "x"}) == {"places": []}

    assert [(c.status_code, c.attempt, c.error_class) for c in metered] == [(200, 1, None)]
    assert metered[0].endpoint == URL
    assert clock.delays == []


def test_the_metered_endpoint_never_carries_the_query_string(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(url__startswith=URL).mock(return_value=httpx.Response(200, json={}))
    client, metered = make_client(clock)

    client.request_json("POST", f"{URL}?key=super-secret&page=2", parse=identity)

    assert metered[0].endpoint == URL


def test_a_connection_failure_is_retried_three_times_then_raises_transient(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(side_effect=httpx.ConnectTimeout("no route"))
    client, metered = make_client(clock)

    with pytest.raises(TransientError) as caught:
        client.request_json("POST", URL, parse=identity)

    assert caught.value.retryable is True
    assert len(metered) == 3
    assert {c.error_class for c in metered} == {"ConnectTimeout"}
    assert {c.status_code for c in metered} == {None}
    # Exponential, with jitter pinned to zero for the assertion.
    assert clock.delays == [0.5, 1.0]


def test_a_500_is_retried_and_then_gives_up_with_a_readable_error(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(
        return_value=httpx.Response(500, json={"error": {"message": "Internal error"}})
    )
    client, metered = make_client(clock)

    with pytest.raises(TransientError) as caught:
        client.request_json("POST", URL, parse=identity)

    assert "500" in str(caught.value)
    assert [c.status_code for c in metered] == [500, 500, 500]


def test_a_500_that_recovers_returns_the_later_body(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    client, metered = make_client(clock)

    assert client.request_json("POST", URL, parse=identity) == {"ok": True}
    assert [c.status_code for c in metered] == [500, 200]


def test_a_429_waits_out_retry_after_and_then_succeeds(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client, metered = make_client(clock)

    assert client.request_json("POST", URL, parse=identity) == {"ok": True}

    assert clock.delays == [2.0]
    assert [(c.status_code, c.attempt) for c in metered] == [(429, 1), (200, 2)]


def test_an_unusable_retry_after_falls_back_to_backoff(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}),
            httpx.Response(200, json={}),
        ]
    )
    client, _ = make_client(clock)

    client.request_json("POST", URL, parse=identity)

    assert clock.delays == [0.5]


def test_retry_after_is_capped_so_a_worker_cannot_be_parked_for_an_hour(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3600"}),
            httpx.Response(200, json={}),
        ]
    )
    client, _ = make_client(clock)

    client.request_json("POST", URL, parse=identity)

    assert clock.delays == [60.0]


def test_a_persistent_429_ends_as_rate_limited(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    client, metered = make_client(clock)

    with pytest.raises(RateLimitedError) as caught:
        client.request_json("POST", URL, parse=identity)

    assert caught.value.retryable is True
    assert len(metered) == 3


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_failure_is_not_retried_and_says_what_to_check(
    mock_http: respx.MockRouter, clock: FakeClock, status: int
) -> None:
    mock_http.post(URL).mock(return_value=httpx.Response(status, json={"error": {"message": "no"}}))
    client, metered = make_client(clock)

    with pytest.raises(AuthError) as caught:
        client.request_json("POST", URL, parse=identity)

    message = str(caught.value)
    assert "GOOGLE_PLACES_API_KEY" in message
    assert "enabled" in message and "billing" in message
    assert caught.value.retryable is False
    assert len(metered) == 1
    assert clock.delays == []


def test_a_400_is_not_retried_and_carries_the_providers_own_message(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Request contains an invalid argument."}}
        )
    )
    client, metered = make_client(clock)

    with pytest.raises(AdapterError) as caught:
        client.request_json("POST", URL, parse=identity)

    assert "Request contains an invalid argument." in str(caught.value)
    assert not isinstance(caught.value, TransientError)
    assert len(metered) == 1


def test_an_unparseable_2xx_body_is_retried_exactly_once_then_raises_schema_error(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    def explode(body: Any) -> Any:
        raise ValueError("places is not a list")

    mock_http.post(URL).mock(return_value=httpx.Response(200, json={"places": "nope"}))
    client, metered = make_client(clock)

    with pytest.raises(SchemaError) as caught:
        client.request_json("POST", URL, parse=explode)

    assert len(metered) == 2, "one original attempt plus exactly one retry"
    assert caught.value.retryable is False
    assert caught.value.details["body"]


def test_a_body_that_parses_on_the_second_try_is_returned(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    seen: list[int] = []

    def parse_second_time(body: Any) -> Any:
        seen.append(1)
        if len(seen) == 1:
            raise ValueError("transient garbage")
        return body

    mock_http.post(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    client, _ = make_client(clock)

    assert client.request_json("POST", URL, parse=parse_second_time) == {"ok": True}


def test_the_stored_error_body_is_truncated(mock_http: respx.MockRouter, clock: FakeClock) -> None:
    mock_http.post(URL).mock(return_value=httpx.Response(400, text="x" * 20_000))
    client, _ = make_client(clock)

    with pytest.raises(AdapterError) as caught:
        client.request_json("POST", URL, parse=identity)

    assert len(caught.value.details["body"]) == MAX_ERROR_BODY_CHARS


def test_a_secret_echoed_by_the_provider_is_redacted_out_of_the_error(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    secret = "super-secret-key-value"
    mock_http.post(URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": f"API key {secret} is not valid"}}
        )
    )
    client, _ = make_client(clock, secrets=[secret])

    with pytest.raises(AdapterError) as caught:
        client.request_json("POST", URL, parse=identity)

    assert secret not in str(caught.value)
    assert secret not in caught.value.details["body"]
    assert "[REDACTED]" in str(caught.value)


def test_the_limiter_is_consulted_before_every_attempt(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    mock_http.post(URL).mock(side_effect=[httpx.Response(500), httpx.Response(200, json={})])
    limiter = StubLimiter()
    client, _ = make_client(clock, limiter=limiter)

    client.request_json("POST", URL, parse=identity)

    assert limiter.acquired == 2


def test_a_quota_refusal_stops_the_call_before_it_is_sent(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    route = mock_http.post(URL).mock(return_value=httpx.Response(200, json={}))
    limiter = StubLimiter(QuotaExceededError("the daily cap for 'example' has been reached"))
    client, metered = make_client(clock, limiter=limiter)

    with pytest.raises(QuotaExceededError):
        client.request_json("POST", URL, parse=identity)

    assert route.call_count == 0
    assert metered == []


def test_a_broken_meter_never_breaks_the_call_it_measures(
    mock_http: respx.MockRouter, clock: FakeClock
) -> None:
    def broken(record: ApiCallRecord) -> None:
        raise RuntimeError("the metering database is down")

    mock_http.post(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = ApiHttpClient(source="example", meter=broken, sleeper=clock.sleep)

    assert client.request_json("POST", URL, parse=identity) == {"ok": True}
