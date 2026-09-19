"""The fake provider answers from fixtures, in order, and says nothing for the unknown."""

import json
from pathlib import Path

from app.modules.ai.client import LLMRequest
from app.modules.ai.fake_client import FIXTURE_ROOT, FakeLLMClient
from app.modules.ai.schema import parse_output


def request(key: str | None, tier: str = "triage") -> LLMRequest:
    return LLMRequest(
        model="fake-triage",
        tier=tier,  # type: ignore[arg-type]
        system="system",
        user="user " * 50,
        schema_name="s",
        json_schema={},
        fixture_key=key,
    )


def test_an_unknown_key_gets_the_minimal_unknown_answer(tmp_path: Path) -> None:
    client = FakeLLMClient(tmp_path)

    result = client.complete(request("nobody.invalid"))

    output = parse_output(result.text)
    assert output.opportunities == []
    assert output.industry == "unknown"
    assert result.tokens_in > 0 and result.tokens_out > 0
    assert client.requests[0].fixture_key == "nobody.invalid"


def test_answers_are_served_in_order_and_the_last_one_repeats(tmp_path: Path) -> None:
    (tmp_path / "site.invalid.json").write_text(
        json.dumps(
            {
                "triage": [
                    {"raw": "not json", "tokens_in": 10, "tokens_out": 2},
                    {"output": {"a": 1}, "tokens_in": 11, "tokens_out": 3},
                ],
                "escalation": [{"output": {"b": 2}}],
            }
        )
    )
    client = FakeLLMClient(tmp_path)

    first = client.complete(request("site.invalid"))
    second = client.complete(request("site.invalid"))
    third = client.complete(request("site.invalid"))
    escalated = client.complete(request("site.invalid", "escalation"))

    assert (first.text, first.tokens_in, first.tokens_out) == ("not json", 10, 2)
    assert json.loads(second.text) == {"a": 1} and second.tokens_in == 11
    assert json.loads(third.text) == {"a": 1}
    assert json.loads(escalated.text) == {"b": 2}
    assert escalated.model == "fake-triage", "the model name is whatever was asked for"


def test_reset_starts_the_script_over(tmp_path: Path) -> None:
    (tmp_path / "k.json").write_text(json.dumps({"triage": [{"raw": "1"}, {"raw": "2"}]}))
    client = FakeLLMClient(tmp_path)
    client.complete(request("k"))
    client.reset()

    assert client.complete(request("k")).text == "1"


def test_every_checked_in_fixture_names_a_tier_and_is_valid_json() -> None:
    files = sorted(FIXTURE_ROOT.glob("*.json"))

    assert files, "the demo needs AI fixtures"
    for path in files:
        if path.name == "expected_opportunities.json":
            continue
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert "triage" in fixture or "escalation" in fixture, path.name
        for tier in ("triage", "escalation"):
            for entry in fixture.get(tier, []):
                assert "raw" in entry or "output" in entry, path.name
