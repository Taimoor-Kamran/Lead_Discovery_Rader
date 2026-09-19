"""Four components and a weighted total (blueprint slide 36): fact is kept apart from inference.

`facts` is what we know about the business itself, `inference` is how sure the pipeline is
about the opportunity, `intent` is whether the business said so itself, and
`contactability` is whether there is a business-level way to reach it. The components are
stored and returned with every opportunity so a salesperson can see *why* a score is what
it is, not just that it is. The weights are assumptions until calibrated.
"""

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.normalization.taxonomy import OTHER

SCORING_VERSION = "scoring-1"
PLACES = 3


@dataclass(frozen=True)
class Weights:
    facts: float
    inference: float
    intent: float
    contactability: float

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Weights":
        config = settings or get_settings()
        return cls(
            facts=config.scoring_weight_facts,
            inference=config.scoring_weight_inference,
            intent=config.scoring_weight_intent,
            contactability=config.scoring_weight_contactability,
        )


@dataclass(frozen=True)
class Score:
    facts: float
    inference: float
    intent: float
    contactability: float
    total: float
    version: str = SCORING_VERSION

    def components(self) -> dict[str, Any]:
        return {
            "facts": self.facts,
            "inference": self.inference,
            "intent": self.intent,
            "contactability": self.contactability,
        }


def facts_component(business: Business, audit: WebsiteAudit | None) -> float:
    """How much is known for sure about the business: four facts, averaged."""
    operational = {
        BusinessStatus.operational: 1.0,
        BusinessStatus.unknown: 0.5,
        BusinessStatus.closed_temporarily: 0.5,
        BusinessStatus.closed_permanently: 0.0,
    }[business.business_status]
    if business.website_kind is WebsiteKind.none:
        website = 1.0  # "no website" is a known fact about the business
    elif audit is not None and audit.status in (AuditStatus.done, AuditStatus.skipped):
        website = 1.0
    else:
        website = 0.0
    industry = 1.0 if business.industry and business.industry != OTHER else 0.0
    location = 1.0 if business.city and business.state else 0.0
    return round((operational + website + industry + location) / 4, PLACES)


def contactability_component(business: Business, audit: WebsiteAudit | None) -> float:
    """Business-level only: a public phone, and a contact form or email link on the homepage."""
    value = 0.5 if business.phone_e164 else 0.0
    checks = (audit.checks if audit is not None else None) or {}
    if any((checks.get(key) or {}).get("value") for key in ("contact_form", "mailto_link")):
        value += 0.5
    return round(value, PLACES)


def score(
    business: Business,
    audit: WebsiteAudit | None,
    *,
    confidence: float,
    intent_explicit: bool,
    weights: Weights | None = None,
) -> Score:
    w = weights or Weights.from_settings()
    facts = facts_component(business, audit)
    inference = round(max(0.0, min(1.0, confidence)), PLACES)
    intent = 1.0 if intent_explicit else 0.0
    contactability = contactability_component(business, audit)
    total = (
        w.facts * facts
        + w.inference * inference
        + w.intent * intent
        + w.contactability * contactability
    )
    return Score(
        facts=facts,
        inference=inference,
        intent=intent,
        contactability=contactability,
        total=round(total, PLACES),
    )
