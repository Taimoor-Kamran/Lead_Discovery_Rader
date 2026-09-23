"""The service catalogue and the deterministic rules: every finding, and the formula."""

import pytest

from app.modules.audit_web.findings import BANNED_WORDS, CATALOGUE, Severity
from app.modules.audit_web.models import AuditStatus
from app.modules.normalization.schemas import BusinessStatus
from app.modules.opportunities import catalogue
from app.modules.opportunities.rules import ADS_SOCIAL_REASON, rule_opportunities
from tests.factories import check, finding, make_audit, make_business

EXPECTED_SERVICE = {
    "no_website": "website_design",
    "social_profile_only": "website_design",
    "unreachable": "website_design",
    "no_https": "website_design",
    "tls_invalid": "website_design",
    "builder_subdomain": "website_design",
    "no_mobile_viewport": "website_design",
    "stale_copyright": "website_design",
    "no_contact_on_homepage": "website_design",
    "slow_mobile": "website_design",
    "js_shell_suspected": "website_design",
    "images_without_alt": "website_design",
    "unlabelled_form_fields": "website_design",
    "thin_content": "website_design",
    "no_section_headings": "website_design",
    "heading_level_skipped": "website_design",
    "missing_title": "seo_gbp",
    "missing_meta_description": "seo_gbp",
    "no_h1": "seo_gbp",
    "no_structured_data": "seo_gbp",
    "no_online_booking": "booking_setup",
    "no_live_chat": "ai_chat_setup",
    "robots_blocked": None,
}


def test_the_catalogue_has_exactly_the_five_services() -> None:
    assert catalogue.service_keys() == [
        "website_design",
        "seo_gbp",
        "booking_setup",
        "ai_chat_setup",
        "ads_social",
    ]


@pytest.mark.parametrize(("code", "service"), sorted(EXPECTED_SERVICE.items()))
def test_every_audit_finding_maps_to_its_service(code: str, service: str | None) -> None:
    assert code in CATALOGUE, "the spec's table names a finding the audit does not produce"
    assert catalogue.service_for_finding(code) == service


def test_no_catalogue_finding_is_left_unmapped_by_accident() -> None:
    unmapped = {code for code in CATALOGUE if catalogue.service_for_finding(code) is None}

    assert unmapped == {"robots_blocked"}


@pytest.mark.parametrize(
    ("confidences", "expected"),
    [
        ([0.8], 0.8),
        ([0.6], 0.6),
        ([0.4], 0.4),
        ([0.2], 0.2),
        ([0.8, 0.6], 0.92),
        ([0.4, 0.4], 0.64),
        ([0.8, 0.8, 0.8], 0.95),  # 0.992, capped
        ([], 0.0),
    ],
)
def test_the_confidence_formula(confidences: list[float], expected: float) -> None:
    assert catalogue.combine(confidences) == expected


def test_severity_gives_the_base_confidence() -> None:
    assert {
        Severity.high: 0.8,
        Severity.medium: 0.6,
        Severity.low: 0.4,
        Severity.info: 0.2,
    } == catalogue.SEVERITY_CONFIDENCE


@pytest.mark.parametrize("code", sorted(code for code, s in EXPECTED_SERVICE.items() if s))
def test_one_finding_yields_one_opportunity_with_verbatim_evidence(code: str) -> None:
    business = make_business()
    audit = make_audit(business, findings=[finding(code)], social_links=["facebook"])

    [opportunity] = rule_opportunities(business, audit)

    assert opportunity.service == EXPECTED_SERVICE[code]
    assert (
        opportunity.confidence == catalogue.SEVERITY_CONFIDENCE[Severity(CATALOGUE[code].severity)]
    )
    assert opportunity.reason == CATALOGUE[code].wording.format(
        url="https://example-plumbing.invalid/",
        year=2016,
        score=41,
        missing=37,
        total=41,
        count=4,
        words=84,
        noun="form fields" if code == "unlabelled_form_fields" else "words",
        higher="h1",
        lower="h3",
    )
    [evidence] = opportunity.evidence
    assert evidence.finding_code == code
    assert evidence.text == f"evidence for {code}"
    assert evidence.url == "https://example-plumbing.invalid/"


def test_findings_are_grouped_by_service_and_combined() -> None:
    business = make_business()
    audit = make_audit(
        business,
        findings=[
            finding("no_https"),
            finding("stale_copyright"),
            finding("missing_meta_description"),
            finding("no_h1"),
            finding("no_online_booking"),
        ],
        social_links=["facebook"],
    )

    by_service = {o.service: o for o in rule_opportunities(business, audit)}

    assert list(by_service) == ["website_design", "seo_gbp", "booking_setup"]
    assert by_service["website_design"].confidence == catalogue.combine([0.8, 0.4])
    assert by_service["seo_gbp"].confidence == catalogue.combine([0.6, 0.4])
    assert by_service["booking_setup"].confidence == 0.6
    assert by_service["website_design"].finding_codes == ["no_https", "stale_copyright"]
    assert len(by_service["website_design"].evidence) == 2
    assert by_service["website_design"].reason.count("Audit found") == 2


def test_ads_social_comes_from_an_empty_social_links_check() -> None:
    business = make_business()
    audit = make_audit(business, social_links=[])

    [opportunity] = rule_opportunities(business, audit)

    assert opportunity.service == "ads_social"
    assert opportunity.confidence == 0.3
    assert opportunity.reason == ADS_SOCIAL_REASON
    assert opportunity.evidence[0].finding_code is None
    assert opportunity.evidence[0].url == "https://example-plumbing.invalid/"


def test_ads_social_needs_a_parsed_page() -> None:
    business = make_business()
    with_links = make_audit(business, social_links=["facebook"])
    unparsed = make_audit(business, checks={"parsed": check(False), "social_links": check([])})

    assert rule_opportunities(business, with_links) == []
    assert rule_opportunities(business, unparsed) == []


def test_robots_blocked_and_closed_permanently_get_nothing() -> None:
    business = make_business()
    blocked = make_audit(
        business, status=AuditStatus.robots_blocked, findings=[finding("robots_blocked")]
    )
    closed = make_business(business_status=BusinessStatus.closed_permanently)
    open_audit = make_audit(closed, findings=[finding("no_https")], social_links=[])

    assert rule_opportunities(business, blocked) == []
    assert rule_opportunities(closed, open_audit) == []


def test_an_unreachable_site_yields_only_website_design() -> None:
    business = make_business()
    audit = make_audit(business, status=AuditStatus.unreachable, findings=[finding("unreachable")])

    [opportunity] = rule_opportunities(business, audit)

    assert opportunity.service == "website_design"
    assert opportunity.finding_codes == ["unreachable"]
    assert opportunity.confidence == 0.8


def test_a_skipped_no_website_audit_yields_website_design_from_the_listing() -> None:
    business = make_business(website=None, domain=None)
    audit = make_audit(
        business, status=AuditStatus.skipped, findings=[finding("no_website", url=None)]
    )

    [opportunity] = rule_opportunities(business, audit)

    assert opportunity.service == "website_design"
    assert opportunity.evidence[0].url is None, "no_website cites our own record"


def test_a_failed_audit_yields_nothing() -> None:
    business = make_business()
    audit = make_audit(business, status=AuditStatus.failed)

    assert rule_opportunities(business, audit) == []


def test_rule_reasons_obey_the_wording_rule() -> None:
    business = make_business()
    audit = make_audit(
        business,
        findings=[finding(code) for code, s in EXPECTED_SERVICE.items() if s],
        social_links=[],
    )

    for opportunity in rule_opportunities(business, audit):
        lowered = opportunity.reason.lower()
        assert not any(word in lowered for word in BANNED_WORDS), opportunity.reason
