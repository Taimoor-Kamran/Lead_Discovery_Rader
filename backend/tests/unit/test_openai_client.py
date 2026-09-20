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
