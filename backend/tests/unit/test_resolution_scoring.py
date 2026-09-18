"""Signals, weights, thresholds and — most of all — the hard rules.

The hard rules are what stop the resolver being confidently wrong. A name is never
enough on its own, and two records that disagree about both their domain and their phone
are not one business however alike they look.
"""

import pytest

from app.modules.businesses.models import Business
from app.modules.normalization.schemas import (
    Address,
    BusinessStatus,
    NormalizedBusiness,
    WebsiteKind,
)
from app.modules.resolution.resolver import DecisionKind, Thresholds, decide, score_candidates
from app.modules.resolution.scoring import (
    MatchScore,
    Signals,
    Weights,
    address_match,
    exact,
    geo_proximity,
    has_conflicting_keys,
    name_similarity,
    score_pair,
)

WEIGHTS = Weights()
THRESHOLDS = Thresholds()


def record(**overrides: object) -> NormalizedBusiness:
    fields: dict[str, object] = {
        "source": "google_places",
        "display_name": "ABC Plumbing, LLC",
        "normalized_name": "abc plumbing",
        "name_key": "ABKPLMBNK",
        "industry": "plumbing",
        "address": Address(
            line1="123 Main Street",
            street_key="123 main street",
            city="Austin",
            state="TX",
            postal_code="78701",
            country="US",
        ),
        "lat": 30.2672,
        "lng": -97.7431,
        "geohash7": "9v6kpvc",
        "phone_e164": "+15125550142",
        "website": "https://abc.example.com/",
        "domain": "abc.example.com",
        "website_kind": WebsiteKind.own_site,
        "business_status": BusinessStatus.operational,
    }
    fields.update(overrides)
    return NormalizedBusiness(**fields)  # type: ignore[arg-type]


def business(**overrides: object) -> Business:
    fields: dict[str, object] = {
        "display_name": "ABC Plumbing LLC",
        "normalized_name": "abc plumbing",
        "name_key": "ABKPLMBNK",
        "street_key": "123 main street",
        "city": "Austin",
        "state": "TX",
        "postal_code": "78701",
        "lat": 30.2672,
        "lng": -97.7431,
        "geohash7": "9v6kpvc",
        "phone_e164": "+15125550142",
        "domain": "abc.example.com",
        "website_kind": WebsiteKind.own_site,
        "business_status": BusinessStatus.operational,
    }
    fields.update(overrides)
    return Business(**fields)


# --- individual signals -------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [("a", "a", 1.0), ("a", "b", 0.0), (None, None, 0.0), ("a", None, 0.0), (None, "a", 0.0)],
)
def test_two_unknowns_never_match(left: str | None, right: str | None, expected: float) -> None:
    assert exact(left, right) == expected


def test_name_similarity_is_token_set_ratio_over_a_hundred() -> None:
    assert name_similarity("abc plumbing", "abc plumbing") == 1.0
    assert 0.5 < name_similarity("abc plumbing", "abc plumbing and hvac") <= 1.0
    assert name_similarity("abc plumbing", "delgado roofing") < 0.5
    assert name_similarity(None, "abc plumbing") == 0.0


def test_address_match_is_one_for_street_and_postal_and_half_for_postal_alone() -> None:
    assert address_match(record(), business()) == 1.0
    assert address_match(record(), business(street_key="9 oak avenue")) == 0.5
    assert address_match(record(), business(postal_code="73301")) == 0.0
    assert address_match(record(), business(postal_code=None)) == 0.0


def test_geo_proximity_is_one_up_close_and_zero_far_away() -> None:
    assert geo_proximity(record(), business()) == 1.0
    assert geo_proximity(record(), business(lat=31.5, lng=-97.0)) == 0.0
    assert geo_proximity(record(), business(lat=None, lng=None)) == 0.0

    middling = geo_proximity(record(), business(lng=-97.7406))  # roughly 240 m east
    assert 0.0 < middling < 1.0


def test_conflicting_keys_need_both_a_different_domain_and_a_different_phone() -> None:
    assert has_conflicting_keys(record(), business(domain="other.example.com")) is False
    assert has_conflicting_keys(record(), business(phone_e164="+15125550199")) is False
    assert (
        has_conflicting_keys(
            record(), business(domain="other.example.com", phone_e164="+15125550199")
        )
        is True
    )
    assert has_conflicting_keys(record(), business(domain=None, phone_e164=None)) is False


# --- the weighted score -------------------------------------------------------------


def test_every_signal_agreeing_scores_one() -> None:
    result = score_pair(record(), business(), WEIGHTS)

    assert result.score == 1.0
    assert result.signals.domain_match == 1.0
    assert result.signals.phone_match == 1.0


def test_nothing_in_common_scores_almost_nothing() -> None:
    """Only the loose name similarity is left, and every strong signal is zero."""
    other = business(
        normalized_name="delgado roofing",
        name_key="TLKTRFNK",
        street_key="9 oak avenue",
        postal_code="73301",
        lat=32.0,
        lng=-96.0,
        phone_e164="+15125550199",
        domain="delgado.example.com",
    )

    result = score_pair(record(), other, WEIGHTS)

    assert result.score < 0.1
    assert result.signals.domain_match == 0.0
    assert result.signals.phone_match == 0.0
    assert result.signals.address_match == 0.0
    assert result.signals.geo_proximity == 0.0


def test_the_score_is_the_weighted_sum_rounded_to_three_decimals() -> None:
    """Domain and name only: 0.30 + 0.20 — nothing else agrees."""
    other = business(
        phone_e164=None, street_key=None, postal_code=None, lat=None, lng=None, geohash7=None
    )

    assert score_pair(record(), other, WEIGHTS).score == 0.5


def test_weights_come_from_configuration_not_from_code() -> None:
    doubled = Weights(domain=0.6, phone=0.0, name=0.0, address=0.0, geo=0.0)

    assert score_pair(record(), business(), doubled).score == 0.6


# --- hard rules ---------------------------------------------------------------------


def test_a_name_alone_is_never_a_strong_key() -> None:
    name_only = MatchScore(score=0.95, signals=Signals(name_similarity=1.0))

    assert name_only.has_strong_key is False


@pytest.mark.parametrize(
    "signals",
    [
        Signals(domain_match=1.0),
        Signals(phone_match=1.0),
        Signals(address_match=1.0, name_similarity=0.9),
    ],
)
def test_a_domain_a_phone_or_an_exact_address_with_the_name_is_strong(signals: Signals) -> None:
    assert MatchScore(score=0.9, signals=signals).has_strong_key is True


def test_an_exact_address_with_a_weak_name_is_not_strong() -> None:
    assert (
        MatchScore(
            score=0.9, signals=Signals(address_match=1.0, name_similarity=0.5)
        ).has_strong_key
        is False
    )


def test_a_conflicting_pair_is_dropped_before_it_can_be_scored() -> None:
    twin = business(domain="other.example.com", phone_e164="+15125550199")

    assert score_candidates(record(), [twin], weights=WEIGHTS) == []


# --- decisions ----------------------------------------------------------------------


def test_no_candidates_means_a_new_business() -> None:
    assert decide(record(), [], weights=WEIGHTS, thresholds=THRESHOLDS).kind is DecisionKind.new


def test_a_strong_high_scoring_match_is_auto_merged() -> None:
    decision = decide(record(), [business()], weights=WEIGHTS, thresholds=THRESHOLDS)

    assert decision.kind is DecisionKind.auto_merge
    assert decision.best is not None
    assert decision.best.match.score >= THRESHOLDS.auto_merge


def test_identical_names_with_nothing_else_shared_never_auto_merge() -> None:
    """The acceptance criterion: no auto-merge on name similarity alone."""
    twin = business(
        phone_e164=None,
        domain=None,
        street_key=None,
        postal_code=None,
        lat=None,
        lng=None,
        geohash7=None,
    )

    decision = decide(record(), [twin], weights=WEIGHTS, thresholds=THRESHOLDS)

    assert decision.kind is not DecisionKind.auto_merge


def test_the_same_name_at_the_same_street_address_is_a_strong_key() -> None:
    """The third strong key from the spec: `address_match = 1.0` and a name at 0.9 or more."""
    twin = business(phone_e164=None, domain=None)
    weights = Weights(domain=0.0, phone=0.0, name=0.55, address=0.4, geo=0.05)

    decision = decide(record(), [twin], weights=weights, thresholds=THRESHOLDS)

    assert decision.kind is DecisionKind.auto_merge
    assert decision.best is not None
    assert decision.best.match.signals.address_match == 1.0


def test_a_high_score_from_the_name_alone_is_capped_at_review() -> None:
    twin = business(
        phone_e164=None, domain=None, street_key=None, postal_code=None, lat=None, lng=None
    )
    weights = Weights(domain=0.0, phone=0.0, name=1.0, address=0.0, geo=0.0)

    decision = decide(record(), [twin], weights=weights, thresholds=THRESHOLDS)

    assert decision.kind is DecisionKind.review
    assert decision.capped_by_hard_rule is True


def test_a_chain_location_sharing_only_a_domain_goes_to_review() -> None:
    """Same domain, different address and phone.

    The weighted score is 0.50 — under the review threshold — so without the shared-key
    floor this pair would quietly become a second business. It has to reach a human.
    """
    other_branch = business(
        phone_e164="+15125550199",
        street_key="9 oak avenue",
        postal_code="73301",
        lat=30.35,
        lng=-97.65,
        geohash7="9v6m000",
    )

    decision = decide(record(), [other_branch], weights=WEIGHTS, thresholds=THRESHOLDS)

    assert decision.kind is DecisionKind.review
    assert decision.best is not None
    assert decision.best.match.score < THRESHOLDS.review
    assert decision.best.match.signals.domain_match == 1.0


def test_a_shared_phone_and_nothing_else_also_reaches_a_human() -> None:
    other = business(
        domain=None,
        normalized_name="delgado roofing",
        street_key=None,
        postal_code=None,
        lat=None,
        lng=None,
    )

    decision = decide(record(), [other], weights=WEIGHTS, thresholds=THRESHOLDS)

    assert decision.kind is DecisionKind.review


def test_a_middling_score_goes_to_review_with_at_most_three_candidates() -> None:
    """Five lookalikes that share a name and a postal code, and no strong key at all."""
    twins = [
        business(domain=None, phone_e164=None, street_key=None, lat=None, lng=None)
        for _ in range(5)
    ]
    weights = Weights(domain=0.0, phone=0.0, name=0.7, address=0.3, geo=0.0)

    decision = decide(record(), twins, weights=weights, thresholds=Thresholds(0.99, 0.6))

    assert decision.kind is DecisionKind.review
    assert len(decision.candidates) == 3


def test_a_score_below_the_review_threshold_and_no_shared_key_is_a_new_business() -> None:
    weak = business(
        normalized_name="totally different",
        phone_e164=None,
        domain=None,
        street_key=None,
        postal_code=None,
        lat=None,
        lng=None,
    )

    assert decide(record(), [weak], weights=WEIGHTS, thresholds=THRESHOLDS).kind is DecisionKind.new


def test_a_closed_business_is_still_eligible_to_match() -> None:
    closed = business(business_status=BusinessStatus.closed_permanently)

    assert (
        decide(record(), [closed], weights=WEIGHTS, thresholds=THRESHOLDS).kind
        is DecisionKind.auto_merge
    )


def test_thresholds_come_from_configuration() -> None:
    twin = business(domain=None, phone_e164=None, street_key=None, lat=None, lng=None)

    lenient = decide(record(), [twin], weights=WEIGHTS, thresholds=Thresholds(0.1, 0.05))
    strict = decide(record(), [twin], weights=WEIGHTS, thresholds=Thresholds(0.99, 0.98))

    assert lenient.kind is DecisionKind.review, "still capped: the pair has no strong key"
    assert strict.kind is DecisionKind.new
