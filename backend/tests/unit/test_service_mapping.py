"""One finding → service mapping, and a `method` on every finding (spec v0.12.0, items 3 and 10).

Before v0.12.0 a finding carried a `service_category` of its own while
`opportunities/catalogue.py` kept a second, hand-written finding → service table. Nothing
read the first; everything that files an opportunity read the second; and they disagreed.
The catalogue in `audit_web/findings.py` is now the only mapping, and these tests keep it
that way.
"""

import string

import pytest

from app.modules.audit_web.findings import CATALOGUE, Method, Service, build
from app.modules.opportunities.catalogue import FINDING_TO_SERVICE, SERVICES, service_for_finding

# The finding → service table as it stood in `opportunities/catalogue.py` before v0.12.0.
# Every opportunity in the production database was filed through exactly this table, so
# pinning it proves the refactor re-files none of them.
PRE_V012_SERVICES = {
    "website_design": {
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
    },
    "seo_gbp": {
        "missing_title",
        "missing_meta_description",
        "no_h1",
        "no_structured_data",
        "few_reviews",
    },
    "booking_setup": {"no_online_booking"},
    "ai_chat_setup": {"no_live_chat"},
}

# The findings whose facts an outside service measured or reported.
API_FINDINGS = {"slow_mobile", "low_accessibility_score", "low_best_practices_score", "few_reviews"}

# The only values a wording template may be filled with. Messages reach the AI step
# unscrubbed, so none of these may ever carry page text, a phone, an email or an address.
SAFE_WORDING_FIELDS = {
    "url",  # the audited URL
    "score",
    "count",
    "noun",
    "missing",
    "total",
    "words",
    "higher",
    "lower",
    "year",
    "platform",  # a label from BUILDER_SIGNATURES
    "detail",  # "user-scalable=no" / "maximum-scale=1"
    "length",
    "types",  # schema.org type names
    "parts",  # "unit", "ZIP code"…
    "listing",  # the listing's website URL
    "site",  # the homepage's own URL
    "domain",  # a placeholder email *domain*, never the address
    "phrase",  # a phrase from FOOTER_/PAGE_PLACEHOLDER_PHRASES
}


def test_no_finding_code_maps_to_two_services() -> None:
    seen: dict[str, str] = {}
    for key, service in SERVICES.items():
        for code in service.finding_codes:
            assert code not in seen, f"{code} is filed under both {seen[code]} and {key}"
            seen[code] = key
    for code, finding in CATALOGUE.items():
        expected = finding.service.value if finding.service is not None else None
        assert seen.get(code) == expected
        assert service_for_finding(code) == expected
        assert FINDING_TO_SERVICE.get(code) == expected


def test_a_built_finding_carries_the_service_it_is_filed_under() -> None:
    finding = build("no_https").as_dict()

    assert finding["service"] == service_for_finding("no_https") == "website_design"
    assert "service_category" not in finding


def test_every_service_a_finding_names_is_a_real_service() -> None:
    for spec in CATALOGUE.values():
        if spec.service is not None:
            assert spec.service.value in SERVICES
    assert {service.value for service in Service} == set(SERVICES)


def test_no_pre_v012_finding_changed_service() -> None:
    """Zero opportunities are re-filed: every old code maps exactly as it did."""
    for service, codes in PRE_V012_SERVICES.items():
        for code in codes:
            assert service_for_finding(code) == service, code


@pytest.mark.parametrize("code", sorted(CATALOGUE))
def test_every_finding_has_the_method_it_was_established_by(code: str) -> None:
    method = CATALOGUE[code].method

    # No audit finding is an AI reading, so `ai` is never expected here.
    assert method is (Method.api if code in API_FINDINGS else Method.deterministic)


@pytest.mark.parametrize("code", sorted(CATALOGUE))
def test_no_wording_can_carry_page_text_or_a_contact_detail(code: str) -> None:
    fields = {name for _, name, _, _ in string.Formatter().parse(CATALOGUE[code].wording) if name}

    assert fields <= SAFE_WORDING_FIELDS, (
        f"'{code}' fills its message with {sorted(fields - SAFE_WORDING_FIELDS)}; quote page "
        "text and contact details in the evidence, which the AI step scrubs"
    )
