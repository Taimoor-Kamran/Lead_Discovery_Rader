"""Merging rules with the AI: it may confirm, raise a little, or add — never remove."""

from typing import Any

from app.modules.ai.schema import AIOutput
from app.modules.audit_web.findings import BANNED_WORDS
from app.modules.opportunities.models import OpportunitySource
from app.modules.opportunities.rules import Evidence, RuleOpportunity
from app.modules.opportunities.service import AI_ONLY_DEFAULT_REASON, merge

URL = "https://example-plumbing.invalid/"


def rule(service: str, confidence: float) -> RuleOpportunity:
    return RuleOpportunity(
        service=service,
        confidence=confidence,
        reason=f"Audit found something about {service}.",
        evidence=[Evidence(finding_code="no_h1", text="evidence", url=URL)],
        finding_codes=["no_h1"],
    )


def ai(*opportunities: tuple[str, float], rationale: str = "Audit found it too.") -> AIOutput:
    items: list[dict[str, Any]] = [
        {
            "service": service,
            "confidence": confidence,
            "rationale": rationale,
            "evidence": [{"finding_code": None, "quote": "quoted", "source_url": URL}],
        }
        for service, confidence in opportunities
    ]
    return AIOutput.model_validate(
        {
            "business_summary": "s",
            "industry": "plumbing",
            "industry_matches_listing": True,
            "opportunities": items,
            "buying_intent": "none_detected",
            "unknowns": [],
            "needs_human_review": True,
        }
    )


def test_rules_only_when_there_is_no_ai_answer() -> None:
    [merged] = merge([rule("seo_gbp", 0.6)], None)

    assert merged.source is OpportunitySource.rules
    assert merged.confidence == 0.6
    assert merged.ai_agrees is None
    assert merged.evidence == [
        {"finding_code": "no_h1", "text": "evidence", "url": URL, "source": "rules"}
    ]


def test_the_ai_never_removes_a_rule_opportunity() -> None:
    [merged] = merge([rule("seo_gbp", 0.6)], ai(("website_design", 0.9)) and ai())

    assert merged.service == "seo_gbp"
    assert merged.source is OpportunitySource.rules
    assert merged.ai_agrees is False
    assert merged.confidence == 0.6


def test_agreement_raises_confidence_by_at_most_015() -> None:
    [merged] = merge([rule("seo_gbp", 0.6)], ai(("seo_gbp", 0.95)))

    assert merged.source is OpportunitySource.rules_and_ai
    assert merged.ai_agrees is True
    assert merged.confidence == 0.75
    assert merged.reason == "Audit found something about seo_gbp. Audit found it too."
    assert [e["source"] for e in merged.evidence] == ["rules", "ai"]
    assert merged.evidence[1] == {
        "finding_code": None,
        "text": "quoted",
        "url": URL,
        "source": "ai",
    }


def test_agreement_below_the_rule_value_keeps_the_rule_value() -> None:
    [merged] = merge([rule("seo_gbp", 0.6)], ai(("seo_gbp", 0.3)))

    assert merged.confidence == 0.6
    assert merged.ai_agrees is True


def test_an_ai_only_opportunity_is_capped_at_06_and_marked_ai() -> None:
    merged = merge([rule("seo_gbp", 0.6)], ai(("seo_gbp", 0.6), ("ads_social", 0.9)))

    [_rules_one, ai_one] = merged
    assert ai_one.service == "ads_social"
    assert ai_one.source is OpportunitySource.ai
    assert ai_one.confidence == 0.6
    assert ai_one.ai_agrees is True
    assert ai_one.reason == "Audit found it too."
    assert ai_one.evidence[0]["source"] == "ai"


def test_a_blanked_rationale_gets_the_neutral_default_reason() -> None:
    [merged] = merge([], ai(("ads_social", 0.4), rationale=""))

    assert merged.reason == AI_ONLY_DEFAULT_REASON
    assert not any(word in AI_ONLY_DEFAULT_REASON.lower() for word in BANNED_WORDS)


def test_confidences_are_rounded_to_three_places() -> None:
    [merged] = merge([rule("seo_gbp", 0.123456)], ai(("seo_gbp", 0.2)))

    assert merged.confidence == 0.2
