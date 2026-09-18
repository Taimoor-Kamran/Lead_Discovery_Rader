"""The decision: link this record, ask a human, or start a new business.

Pure logic on purpose — it takes a normalized record and the businesses it might be, and
returns what should happen. Everything that touches the database lives in `service.py`.
"""

import enum
from dataclasses import dataclass

from app.core.config import get_settings
from app.modules.businesses.models import Business
from app.modules.normalization.schemas import NormalizedBusiness
from app.modules.resolution.scoring import MatchScore, Weights, score_pair

# How many candidates a reviewer is shown for one record.
REVIEW_CANDIDATE_LIMIT = 3


class DecisionKind(enum.StrEnum):
    auto_merge = "auto_merge"
    review = "review"
    new = "new"


@dataclass(frozen=True)
class ScoredCandidate:
    business: Business
    match: MatchScore


@dataclass(frozen=True)
class Thresholds:
    auto_merge: float = 0.85
    review: float = 0.60

    @classmethod
    def from_settings(cls) -> "Thresholds":
        settings = get_settings()
        return cls(auto_merge=settings.resolution_auto_merge, review=settings.resolution_review)


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    best: ScoredCandidate | None = None
    candidates: tuple[ScoredCandidate, ...] = ()
    # True when the score alone would have merged but a hard rule held it back.
    capped_by_hard_rule: bool = False


def score_candidates(
    normalized: NormalizedBusiness,
    businesses: list[Business],
    *,
    weights: Weights | None = None,
) -> list[ScoredCandidate]:
    """Score every candidate, dropping the ones a hard rule rules out entirely."""
    used = weights or Weights.from_settings()
    scored = [
        ScoredCandidate(business=business, match=score_pair(normalized, business, used))
        for business in businesses
    ]
    kept = [item for item in scored if not item.match.conflicting_keys]
    # A stable sort keeps the blocking order (domain, then phone, then postal, then map
    # cell) as the tiebreak, so equal scores always decide the same way.
    return sorted(kept, key=lambda item: -item.match.score)


def decide(
    normalized: NormalizedBusiness,
    businesses: list[Business],
    *,
    weights: Weights | None = None,
    thresholds: Thresholds | None = None,
) -> Decision:
    """Apply the thresholds and the hard rules to the scored candidates."""
    limits = thresholds or Thresholds.from_settings()
    scored = score_candidates(normalized, businesses, weights=weights)
    if not scored:
        return Decision(kind=DecisionKind.new)

    best = scored[0]
    if best.match.score >= limits.auto_merge and best.match.has_strong_key:
        return Decision(kind=DecisionKind.auto_merge, best=best, candidates=(best,))

    reviewable = [item for item in scored if _is_reviewable(item, limits)]
    if not reviewable:
        return Decision(kind=DecisionKind.new)

    return Decision(
        kind=DecisionKind.review,
        best=reviewable[0],
        candidates=tuple(reviewable[:REVIEW_CANDIDATE_LIMIT]),
        # True when the score alone would have merged and only a hard rule stopped it.
        capped_by_hard_rule=best.match.score >= limits.auto_merge,
    )


def _is_reviewable(candidate: ScoredCandidate, limits: Thresholds) -> bool:
    """Whether a pair is worth a human's time even if the weighted score is low.

    A shared domain or a shared phone always is. Two locations of one chain share a
    domain and nothing else, which scores below the review threshold — and quietly
    creating a second business for them is exactly the mistake this gate exists to stop.
    """
    if candidate.match.score >= limits.review:
        return True
    return candidate.match.signals.domain_match == 1.0 or candidate.match.signals.phone_match == 1.0
