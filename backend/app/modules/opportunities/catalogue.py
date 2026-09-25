"""The agency's services and the audit findings that point at each one. Data, not code.

Adding a service is a new `ServiceSpec` here plus its key in `audit_web.findings.Service`,
which is where findings are filed under it: the rules, the merge, the schema sent to the
model and the API all read this table. Every number in it is a
starting assumption to be calibrated once real opportunities have been reviewed, and is
therefore written down where it can be argued about.
"""

from dataclasses import dataclass

from app.modules.audit_web.findings import CATALOGUE, Service, Severity

# Rule opportunities carry `source = "rules"`; an opportunity the AI proposed with valid
# evidence and no matching rule carries `source = "ai"`; one both found is `rules+ai`.
RULES_SOURCE = "rules"
AI_SOURCE = "ai"
BOTH_SOURCE = "rules+ai"

# Base confidence one finding contributes, by its severity. Combined per service with
# `1 - Π(1 - c)`: two independent weak signals add up, but never beyond the cap.
SEVERITY_CONFIDENCE: dict[Severity, float] = {
    Severity.high: 0.8,
    Severity.medium: 0.6,
    Severity.low: 0.4,
    Severity.info: 0.2,
}
MAX_RULE_CONFIDENCE = 0.95
# `ads_social` from "no social links on the homepage" is a weak signal: fixed and low.
ADS_SOCIAL_CONFIDENCE = 0.3
# What the AI may do to a confidence: raise a rule's by at most this, with valid evidence.
AI_MAX_RAISE = 0.15
# An opportunity only the AI proposed can never be more than this sure of itself.
AI_ONLY_MAX_CONFIDENCE = 0.6

# Audit outcomes that yield no opportunity at all: nothing was read, so nothing is known.
NO_OPPORTUNITY_FINDINGS = frozenset({"robots_blocked"})


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    name: str

    @property
    def finding_codes(self) -> tuple[str, ...]:
        """The findings filed under this service, in catalogue order. Derived, never listed.

        The finding → service mapping lives in one place, `audit_web.findings.CATALOGUE`
        (spec v0.12.0, item 3). This used to be a second, hand-kept list that disagreed
        with the finding's own `service_category`.
        """
        return tuple(code for code, spec in CATALOGUE.items() if spec.service == self.key)


SERVICES: dict[str, ServiceSpec] = {
    spec.key: spec
    for spec in (
        ServiceSpec(Service.website_design.value, "Website design / redesign"),
        ServiceSpec(Service.seo_gbp.value, "SEO / Google Business Profile"),
        ServiceSpec(Service.booking_setup.value, "Online booking setup"),
        ServiceSpec(Service.ai_chat_setup.value, "Chat assistant"),
        # Triggered by a check, not a finding: `checks.social_links.value == []`.
        ServiceSpec(Service.ads_social.value, "Ads (Google/Meta) & social media"),
    )
}

FINDING_TO_SERVICE: dict[str, str] = {
    code: spec.service.value for code, spec in CATALOGUE.items() if spec.service is not None
}
ADS_SOCIAL = "ads_social"


def service_keys() -> list[str]:
    return list(SERVICES)


def is_service(key: str) -> bool:
    return key in SERVICES


def service_for_finding(code: str) -> str | None:
    return FINDING_TO_SERVICE.get(code)


def combine(confidences: list[float]) -> float:
    """`1 - Π(1 - cᵢ)`, capped. Independent signals, so they add up but never to certainty."""
    remaining = 1.0
    for value in confidences:
        remaining *= 1.0 - max(0.0, min(1.0, value))
    return round(min(1.0 - remaining, MAX_RULE_CONFIDENCE), 3)
