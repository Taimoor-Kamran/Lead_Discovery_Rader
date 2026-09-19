"""Signals, weights and the score they add up to (blueprint slide 32).

Every signal runs from 0 to 1 and is stored alongside the score, so a reviewer can always see
*why* a pair scored what it did rather than being handed a number.
"""

from dataclasses import asdict, dataclass

from rapidfuzz import fuzz

from app.core.config import get_settings
from app.modules.businesses.models import Business
from app.modules.normalization.geo import distance_m
from app.modules.normalization.schemas import NormalizedBusiness

# Metres at which `geo_proximity` stops being 1.0, and at which it reaches 0.
NEAR_M = 50.0
FAR_M = 500.0
# What "the same name" has to mean before an address alone may auto-merge a pair.
STRONG_NAME_SIMILARITY = 0.9


@dataclass(frozen=True)
class Weights:
    """How much each signal is worth. Configuration, not a constant."""

    domain: float = 0.30
    phone: float = 0.30
    name: float = 0.20
    address: float = 0.15
    geo: float = 0.05

    @classmethod
    def from_settings(cls) -> "Weights":
        settings = get_settings()
        return cls(
            domain=settings.resolution_weight_domain,
            phone=settings.resolution_weight_phone,
            name=settings.resolution_weight_name,
            address=settings.resolution_weight_address,
            geo=settings.resolution_weight_geo,
        )


@dataclass(frozen=True)
class Signals:
    domain_match: float = 0.0
    phone_match: float = 0.0
    name_similarity: float = 0.0
    address_match: float = 0.0
    geo_proximity: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class MatchScore:
    score: float
    signals: Signals
    # Both sides claim a domain and a phone, and both disagree: not one business.
    conflicting_keys: bool = False

    @property
    def has_strong_key(self) -> bool:
        """Whether anything other than the name ties the pair together.

        Without one, a perfect name match is still only a suggestion — "Joe's Plumbing"
        is the name of a great many unrelated businesses.
        """
        return (
            self.signals.domain_match == 1.0
            or self.signals.phone_match == 1.0
            or (
                self.signals.address_match == 1.0
                and self.signals.name_similarity >= STRONG_NAME_SIMILARITY
            )
        )


def exact(left: str | None, right: str | None) -> float:
    """1.0 only when both sides know the value and agree. Unknown never matches unknown."""
    if not left or not right:
        return 0.0
    return 1.0 if left == right else 0.0


def name_similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return round(float(fuzz.token_set_ratio(left, right)) / 100.0, 4)


def address_match(normalized: NormalizedBusiness, business: Business) -> float:
    """Same street and postal code is 1.0; the same postal code alone is half that."""
    postal = exact(normalized.address.postal_code, business.postal_code)
    if postal == 0.0:
        return 0.0
    if exact(normalized.address.street_key, business.street_key) == 1.0:
        return 1.0
    return 0.5


def geo_proximity(normalized: NormalizedBusiness, business: Business) -> float:
    """1.0 within 50 m, falling linearly to 0 at 500 m. Unknown coordinates score 0."""
    metres = distance_m(normalized.lat, normalized.lng, business.lat, business.lng)
    if metres is None or metres >= FAR_M:
        return 0.0
    if metres <= NEAR_M:
        return 1.0
    return round((FAR_M - metres) / (FAR_M - NEAR_M), 4)


def has_conflicting_keys(normalized: NormalizedBusiness, business: Business) -> bool:
    """Different domains *and* different phones, both sides known: never the same business."""
    domains_differ = bool(
        normalized.domain and business.domain and normalized.domain != business.domain
    )
    phones_differ = bool(
        normalized.phone_e164
        and business.phone_e164
        and normalized.phone_e164 != business.phone_e164
    )
    return domains_differ and phones_differ


def score_pair(
    normalized: NormalizedBusiness, business: Business, weights: Weights | None = None
) -> MatchScore:
    """Weighted sum of every signal, rounded to three decimals."""
    used = weights or Weights.from_settings()
    signals = Signals(
        domain_match=exact(normalized.domain, business.domain),
        phone_match=exact(normalized.phone_e164, business.phone_e164),
        name_similarity=name_similarity(normalized.normalized_name, business.normalized_name),
        address_match=address_match(normalized, business),
        geo_proximity=geo_proximity(normalized, business),
    )
    total = (
        signals.domain_match * used.domain
        + signals.phone_match * used.phone
        + signals.name_similarity * used.name
        + signals.address_match * used.address
        + signals.geo_proximity * used.geo
    )
    return MatchScore(
        score=round(total, 3),
        signals=signals,
        conflicting_keys=has_conflicting_keys(normalized, business),
    )
