"""The OpenAI provider: strict schema in the request, tokens out of the response, errors typed.

Every request is answered by respx; nothing here can reach api.openai.com.
"""

import json
from typing import Any

import httpx
import pytest
import respx

from app.core.http import ApiCallRecord
from app.modules.ai.client import LLMError, LLMRequest
from app.modules.ai.openai_client import OPENAI_ENDPOINT, OpenAIClient, openai_source_config

KEY = "sk-test-SENTINEL-never-log-me-0123456789"  # secrets-hygiene: allow (a test sentinel)


def request() -> LLMRequest:
    return LLMRequest(
        model="triage-model",
        tier="triage",
        system="be careful",
        user="the input",
        schema_name="opportunity_classification",
        json_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )


def completion(content: str, *, prompt: int = 120, completion_tokens: int = 40) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "triage-model-2026",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt + completion_tokens,
        },
    }


def client(meter: Any = None, **kwargs: Any) -> OpenAIClient:
    return OpenAIClient(api_key=KEY, max_retries=0, meter=meter, **kwargs)


def test_a_completion_is_returned_with_its_tokens_and_metered(mock_http: respx.MockRouter) -> None:
    route = mock_http.post(OPENAI_ENDPOINT).mock(
        return_value=httpx.Response(200, json=completion('{"ok": true}'))
    )
    calls: list[ApiCallRecord] = []

    result = client(meter=calls.append).complete(request())

    assert result.text == '{"ok": true}'
    assert (result.tokens_in, result.tokens_out) == (120, 40)
    assert result.model == "triage-model-2026"
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "triage-model"
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["messages"][0] == {"role": "system", "content": "be careful"}
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    [record] = calls
    assert record.source == "openai" and record.status_code == 200
    assert record.endpoint == OPENAI_ENDPOINT


def test_a_429_is_a_retryable_error_and_a_401_is_not(mock_http: respx.MockRouter) -> None:
    mock_http.post(OPENAI_ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "slow down"}}),
            httpx.Response(401, json={"error": {"message": "bad key"}}),
        ]
    )
    calls: list[ApiCallRecord] = []
    api = client(meter=calls.append)

    with pytest.raises(LLMError) as limited:
        api.complete(request())
    with pytest.raises(LLMError) as denied:
        api.complete(request())

    assert limited.value.retryable is True and limited.value.status_code == 429
    assert denied.value.retryable is False and denied.value.status_code == 401
    assert [c.status_code for c in calls] == [429, 401]
    assert KEY not in str(denied.value)


def test_the_sdk_retries_a_429_when_allowed(mock_http: respx.MockRouter) -> None:
    route = mock_http.post(OPENAI_ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "x"}}),
            httpx.Response(200, json=completion("{}")),
        ]
    )

    result = OpenAIClient(api_key=KEY, max_retries=1).complete(request())

    assert result.text == "{}"
    assert route.call_count == 2


def test_a_connection_failure_is_retryable(mock_http: respx.MockRouter) -> None:
    mock_http.post(OPENAI_ENDPOINT).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(LLMError) as info:
        client().complete(request())

    assert info.value.retryable is True


def test_a_refusal_is_an_error_not_an_answer(mock_http: respx.MockRouter) -> None:
    body = completion("")
    body["choices"][0]["message"]["refusal"] = "I cannot help with that"
    body["choices"][0]["message"]["content"] = None
    mock_http.post(OPENAI_ENDPOINT).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(LLMError, match="refused"):
        client().complete(request())


def test_an_empty_key_refuses_to_build() -> None:
    with pytest.raises(LLMError):
        OpenAIClient(api_key="")


def test_the_limiter_is_asked_before_every_call(mock_http: respx.MockRouter) -> None:
    mock_http.post(OPENAI_ENDPOINT).mock(return_value=httpx.Response(200, json=completion("{}")))

    class Limiter:
        acquired = 0

        def acquire(self) -> None:
            self.acquired += 1

    limiter = Limiter()
    client(limiter=limiter).complete(request())

    assert limiter.acquired == 1


def test_the_source_row_config_is_marked_as_a_service() -> None:
    from app.core.config import Settings

    config = openai_source_config(Settings(ai_rps=2.0, ai_daily_call_cap=500))

    assert config["role"] == "ai_service"
    assert config["rate_limit"] == {"requests_per_second": 2.0, "burst": 2, "daily_call_cap": 500}
    assert "never a phone number" in config["commercial_use_note"]


def test_the_request_body_carries_no_temperature_and_no_max_tokens(
    mock_http: respx.MockRouter,
) -> None:
    """The production 400 (spec v0.9.0): this model family rejects both parameters.

    `temperature: 0` → "Unsupported value: 'temperature' does not support 0 with this
    model. Only the default (1) value is supported." `max_tokens` → "Unsupported
    parameter: 'max_tokens' is not supported with this model. Use
    'max_completion_tokens' instead." Fourteen of eighteen businesses failed this way
    and nothing was ever billed, because nothing reached the model.

    `temperature` is omitted rather than pinned to 1: the default is the only value the
    model accepts, and sending it buys nothing while being one more thing to change when
    the next family arrives. No output cap is sent at all — see the spec note — so the
    assertion is that neither spelling of one appears.
    """
    route = mock_http.post(OPENAI_ENDPOINT).mock(
        return_value=httpx.Response(200, json=completion("{}"))
    )

    client().complete(request())

    sent = json.loads(route.calls[0].request.content)
    assert "temperature" not in sent, "this model family accepts only the default"
    assert "max_tokens" not in sent, "if an output cap is ever added it must be the new name"
    # The strict JSON schema is the half of the request that was always fine; keeping it
    # asserted here means a fix to the parameters cannot quietly drop it.
    assert sent["response_format"]["json_schema"]["strict"] is True


def test_a_400_stores_the_body_that_names_the_offending_parameter(
    mock_http: respx.MockRouter,
) -> None:
    """The stored error was "OpenAI answered 400: BadRequestError" for all 14 failures.

    The response body — the one thing that names the parameter — was discarded, so
    diagnosing this needed a manual reproduction against the live API that the stored
    provenance should have made unnecessary.
    """
    mock_http.post(OPENAI_ENDPOINT).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": (
                        "Unsupported value: 'temperature' does not support 0 with this "
                        "model. Only the default (1) value is supported."
                    ),
                    "type": "invalid_request_error",
                    "param": "temperature",
                    "code": "unsupported_value",
                }
            },
        )
    )

    with pytest.raises(LLMError) as info:
        client().complete(request())

    stored = str(info.value)
    assert "400" in stored
    assert "does not support 0 with this model" in stored, "the body must survive"
    assert "param=temperature" in stored, "and must name the parameter"
    assert "code=unsupported_value" in stored
    assert info.value.retryable is False, "a malformed request is not worth retrying"
    assert KEY not in stored


def test_a_failed_call_reports_how_long_it_took(mock_http: respx.MockRouter) -> None:
    """`latency_ms` read 0 on every one of the production failures.

    A failure costs real time — a timeout, or a round trip that ended in a 400 — and the
    classification row recorded none of it, because the latency lived only on the success
    path. A run that spent a minute failing looked instant.
    """
    mock_http.post(OPENAI_ENDPOINT).mock(
        return_value=httpx.Response(400, json={"error": {"message": "no", "param": "temperature"}})
    )

    with pytest.raises(LLMError) as info:
        client().complete(request())

    assert info.value.latency_ms >= 0
    assert isinstance(info.value.latency_ms, int)


def test_an_error_body_is_truncated(mock_http: respx.MockRouter) -> None:
    """A provider that answers with a wall of text must not bloat the `error` column."""
    from app.modules.ai.openai_client import ERROR_BODY_MAX_CHARS

    mock_http.post(OPENAI_ENDPOINT).mock(
        return_value=httpx.Response(400, json={"error": {"message": "x" * 5_000}})
    )

    with pytest.raises(LLMError) as info:
        client().complete(request())

    body = str(info.value).split("BadRequestError: ", 1)[1]
    assert len(body) <= ERROR_BODY_MAX_CHARS
