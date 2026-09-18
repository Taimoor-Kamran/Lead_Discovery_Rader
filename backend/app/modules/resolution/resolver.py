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
    if best.match.score < limits.review:
        return Decision(kind=DecisionKind.new)

    if best.match.score >= limits.auto_merge:
        if best.match.has_strong_key:
            return Decision(kind=DecisionKind.auto_merge, best=best, candidates=(best,))
        # A name is never enough on its own, however similar it is.
        return Decision(
            kind=DecisionKind.review,
            best=best,
            candidates=tuple(scored[:REVIEW_CANDIDATE_LIMIT]),
            capped_by_hard_rule=True,
        )

    return Decision(
        kind=DecisionKind.review, best=best, candidates=tuple(scored[:REVIEW_CANDIDATE_LIMIT])
    )
