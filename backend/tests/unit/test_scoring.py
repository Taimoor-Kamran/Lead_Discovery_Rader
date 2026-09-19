"""Four components, always visible, and the weighted total."""

from app.modules.audit_web.models import AuditStatus
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.opportunities.scoring import (
    SCORING_VERSION,
    Weights,
    contactability_component,
    facts_component,
    score,
)
from tests.factories import check, make_audit, make_business

WEIGHTS = Weights(facts=0.25, inference=0.45, intent=0.10, contactability=0.20)


def test_a_fully_known_reachable_business_has_facts_one() -> None:
    business = make_business()

    assert facts_component(business, make_audit(business)) == 1.0


def test_each_fact_is_worth_a_quarter() -> None:
    business = make_business(business_status=BusinessStatus.unknown)
    assert facts_component(business, make_audit(business)) == 0.875

    business = make_business(business_status=BusinessStatus.closed_permanently, industry="other")
    assert facts_component(business, make_audit(business)) == 0.5

    business = make_business(city=None)
    unreachable = make_audit(business, status=AuditStatus.unreachable)
    assert facts_component(business, unreachable) == 0.5


def test_no_website_is_itself_a_known_fact() -> None:
    business = make_business(website=None, domain=None, website_kind=WebsiteKind.none)

    assert facts_component(business, make_audit(business, status=AuditStatus.skipped)) == 1.0
    assert facts_component(business, None) == 1.0


def test_contactability_is_business_level_only() -> None:
    business = make_business()
    bare = make_audit(business)
    with_form = make_audit(business, checks={"contact_form": check("<form>")})
    with_mail = make_audit(business, checks={"mailto_link": check("mailto:x")})

    assert contactability_component(business, bare) == 0.5
    assert contactability_component(business, with_form) == 1.0
    assert contactability_component(business, with_mail) == 1.0
    assert contactability_component(make_business(phone_e164=None), bare) == 0.0
    assert contactability_component(make_business(phone_e164=None), None) == 0.0


def test_the_total_is_the_weighted_sum_rounded_to_three_places() -> None:
    business = make_business()
    audit = make_audit(business, checks={"contact_form": check("<form>")})

    result = score(business, audit, confidence=0.92, intent_explicit=False, weights=WEIGHTS)

    assert result.facts == 1.0
    assert result.inference == 0.92
    assert result.intent == 0.0
    assert result.contactability == 1.0
    assert result.total == round(0.25 + 0.45 * 0.92 + 0.20, 3) == 0.864
    assert result.version == SCORING_VERSION == "scoring-1"
    assert set(result.components()) == {"facts", "inference", "intent", "contactability"}


def test_intent_is_all_or_nothing() -> None:
    business = make_business()
    audit = make_audit(business)

    explicit = score(business, audit, confidence=0.5, intent_explicit=True, weights=WEIGHTS)
    none = score(business, audit, confidence=0.5, intent_explicit=False, weights=WEIGHTS)

    assert explicit.intent == 1.0 and none.intent == 0.0
    assert round(explicit.total - none.total, 3) == 0.1


def test_weights_come_from_settings() -> None:
    from app.core.config import Settings

    weights = Weights.from_settings(
        Settings(
            scoring_weight_facts=0.1,
            scoring_weight_inference=0.6,
            scoring_weight_intent=0.1,
            scoring_weight_contactability=0.2,
        )
    )

    assert weights == Weights(0.1, 0.6, 0.1, 0.2)
