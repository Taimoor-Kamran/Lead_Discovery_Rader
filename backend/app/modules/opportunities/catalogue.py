"""The agency's services and the audit findings that point at each one. Data, not code.

Adding a service is a new `ServiceSpec` here and nothing else: the rules, the merge, the
schema sent to the model and the API all read this table. Every number in it is a
starting assumption to be calibrated once real opportunities have been reviewed, and is
therefore written down where it can be argued about.
"""

from dataclasses import dataclass

from app.modules.audit_web.findings import Severity

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
    finding_codes: tuple[str, ...]


SERVICES: dict[str, ServiceSpec] = {
    spec.key: spec
    for spec in (
        ServiceSpec(
            "website_design",
            "Website design / redesign",
            (
                "no_website",
                "social_profile_only",
                "unreachable",
                "no_https",
                "tls_invalid",
                "builder_subdomain",
                "no_mobile_viewport",
                "stale_copyright",
                "no_contact_on_homepage",
                "slow_mobile",
                "js_shell_suspected",
                "images_without_alt",
                "unlabelled_form_fields",
                "thin_content",
                "no_section_headings",
                "heading_level_skipped",
                "low_accessibility_score",
                "low_best_practices_score",
            ),
        ),
        ServiceSpec(
            "seo_gbp",
            "SEO / Google Business Profile",
            (
                "missing_title",
                "missing_meta_description",
                "no_h1",
                "no_structured_data",
                "few_reviews",
            ),
        ),
        ServiceSpec("booking_setup", "Online booking setup", ("no_online_booking",)),
        ServiceSpec("ai_chat_setup", "Chat assistant", ("no_live_chat",)),
        # Triggered by a check, not a finding: `checks.social_links.value == []`.
        ServiceSpec("ads_social", "Ads (Google/Meta) & social media", ()),
    )
}

FINDING_TO_SERVICE: dict[str, str] = {
    code: spec.key for spec in SERVICES.values() for code in spec.finding_codes
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
