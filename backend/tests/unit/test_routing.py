"""Triage first; the escalation model only when a rule says the case is unclear."""

from typing import Any

from app.modules.ai.routing import decide
from app.modules.ai.schema import AIOutput


def output(confidences: list[float], *, matches: bool = True) -> AIOutput:
    opportunities: list[dict[str, Any]] = [
        {
            "service": "seo_gbp",
            "confidence": c,
            "rationale": "",
            "evidence": [{"finding_code": None, "quote": "x", "source_url": "https://a/"}],
        }
        for c in confidences
    ]
    return AIOutput.model_validate(
        {
            "business_summary": "s",
            "industry": "plumbing",
            "industry_matches_listing": matches,
            "opportunities": opportunities,
            "buying_intent": "none_detected",
            "unknowns": [],
            "needs_human_review": True,
        }
    )


def test_a_clear_case_is_never_escalated() -> None:
    decision = decide(output=output([0.39, 0.61, 0.9]), schema_invalid=False, finding_codes=[])

    assert decision.escalate is False
    assert decision.reasons == ()


def test_a_confidence_between_040_and_060_escalates() -> None:
    for value in (0.40, 0.5, 0.60):
        decision = decide(output=output([0.9, value]), schema_invalid=False, finding_codes=[])

        assert decision.escalate is True, value
        assert decision.reasons == ("unclear_confidence",)


def test_schema_invalid_after_retry_escalates() -> None:
    decision = decide(output=None, schema_invalid=True, finding_codes=[])

    assert decision.escalate is True
    assert decision.reasons == ("schema_invalid_after_retry",)


def test_a_js_shell_escalates() -> None:
    decision = decide(
        output=output([0.9]), schema_invalid=False, finding_codes=["no_h1", "js_shell_suspected"]
    )

    assert decision.reasons == ("js_shell_suspected",)


def test_an_industry_mismatch_escalates() -> None:
    decision = decide(output=output([0.9], matches=False), schema_invalid=False, finding_codes=[])

    assert decision.reasons == ("industry_mismatch",)


def test_every_reason_is_reported_together() -> None:
    decision = decide(
        output=output([0.5], matches=False),
        schema_invalid=False,
        finding_codes=["js_shell_suspected"],
    )

    assert decision.reasons == ("js_shell_suspected", "unclear_confidence", "industry_mismatch")


def test_escalation_can_be_switched_off_but_the_reasons_remain() -> None:
    decision = decide(output=output([0.5]), schema_invalid=False, finding_codes=[], enabled=False)

    assert decision.escalate is False
    assert decision.reasons == ("unclear_confidence",)
