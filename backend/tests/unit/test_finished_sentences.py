"""v0.12.0 — every new finding against a fixture page, and every comparison as a matched pair.

The pages live in `tests/fixtures/pages/`, one per case, and each is audited the way a real
homepage is: fetch checks, the one HTML parse, the listing comparison, then the rules. A
case states what must be reported *and* what must not, because half of this spec is about
findings that used to be false: a page that carries its LocalBusiness as microdata must not
be told it has none, and a site whose phone agrees with its listing must not be accused of
disagreeing with it.

Every comparison is tested as a pair — the same page against a listing that agrees and one
that does not — so a comparison that fires on agreement fails the build.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.safe_fetch import FetchOutcome
from app.modules.audit_web.checks import Checks, analyse_html, fetch_checks, value_of
from app.modules.audit_web.findings import FindingContext, codes, for_page
from app.modules.audit_web.listing import Listing, compare, text_names_business
from app.modules.normalization.schemas import WebsiteKind

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
URL = "https://ultimateplumber.test/"

# The listing every fixture agrees with, unless a test changes one field of it.
LISTING = Listing(
    name="Ultimate Plumber",
    phone_e164="+15125550100",
    address_line1="1234 Congress Avenue",
    address_line2="Suite 11",
    city="Austin",
    state="TX",
    postal_code="78701",
    country="US",
    website=URL,
)
STRUCTURED = {"no_structured_data", "no_local_business_schema", "invalid_structured_data"}
TITLES = {"default_title", "short_title", "long_title"}


def checks_for(html: str, *, listing: Listing = LISTING, url: str = URL) -> Checks:
    outcome = FetchOutcome(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=html.encode(),
        text=html,
    )
    checks = fetch_checks(outcome)
    checks.update(analyse_html(outcome, now=NOW)[0])
    checks["listing_comparison"] = compare(checks, listing)
    return checks


def found(fixture: str, *, listing: Listing = LISTING, url: str = URL) -> list[str]:
    html = (PAGES / fixture).read_text(encoding="utf-8")
    return codes(for_page(checks_for(html, listing=listing, url=url), None, context(listing)))


def context(listing: Listing = LISTING, **overrides: Any) -> FindingContext:
    values: dict[str, Any] = {
        "industry": "plumbing",
        "website_kind": WebsiteKind.own_site,
        "website": listing.website,
        "booking_industries": frozenset({"plumbing"}),
        "slow_mobile_score": 50,
        "stale_copyright_years": 3,
        "now": NOW,
        "business_name": listing.name,
    }
    values.update(overrides)
    return FindingContext(**values)


# --- one fixture page per finding -----------------------------------------------------------

CASES: list[tuple[str, set[str], set[str]]] = [
    # The four structured-data states, and two pages that must not be called broken or bare.
    ("sd_none.html", {"no_structured_data"}, STRUCTURED - {"no_structured_data"}),
    (
        "sd_organization_only.html",
        {"no_local_business_schema"},
        STRUCTURED - {"no_local_business_schema"},
    ),
    (
        "sd_invalid_json_ld.html",
        {"invalid_structured_data"},
        STRUCTURED - {"invalid_structured_data"},
    ),
    ("sd_microdata.html", set(), STRUCTURED),
    ("sd_rdfa.html", set(), STRUCTURED),
    ("sd_json_ld_in_cdata.html", set(), STRUCTURED),
    # Copyright.
    ("copyright_future.html", {"future_copyright"}, {"stale_copyright"}),
    ("copyright_range_current.html", set(), {"future_copyright", "stale_copyright"} | TITLES),
    ("copyright_range_future.html", {"future_copyright"}, {"stale_copyright"}),
    ("copyright_street_number.html", set(), {"future_copyright", "stale_copyright"}),
    # Title.
    ("title_template.html", {"default_title"}, {"short_title"}),
    ("title_short.html", {"short_title"}, {"default_title", "long_title"}),
    ("title_long.html", {"long_title"}, {"default_title", "short_title"}),
    # Viewport.
    ("viewport_blocks_zoom.html", {"viewport_blocks_zoom"}, {"no_mobile_viewport"}),
    ("viewport_allows_zoom.html", set(), {"viewport_blocks_zoom", "no_mobile_viewport"}),
    # A builder on the business's own domain, and a page that merely links to one.
    ("builder_wix_own_domain.html", {"site_builder"}, {"builder_subdomain"}),
    ("builder_link_only.html", set(), {"site_builder"}),
    # Headings.
    ("multiple_h1.html", {"multiple_h1"}, {"no_h1"}),
    ("copyright_range_current.html", set(), {"multiple_h1"}),
    # Contact details.
    ("placeholder_email.html", {"placeholder_email"}, set()),
    ("real_email_at_email_com.html", set(), {"placeholder_email"}),
    ("placeholder_footer_text.html", {"placeholder_text"}, set()),
    ("placeholder_lorem_ipsum.html", {"placeholder_text"}, set()),
    ("footer_form_label.html", set(), {"placeholder_text"}),
    ("no_tel_with_form.html", {"no_click_to_call"}, {"no_contact_on_homepage"}),
    ("no_contact_at_all.html", {"no_contact_on_homepage"}, {"no_click_to_call"}),
    # The client's own example: every finding their verification report asked for.
    (
        "ultimate_plumber.html",
        {
            "default_title",
            "placeholder_email",
            "placeholder_text",
            "site_builder",
            "future_copyright",
            "no_click_to_call",
        },
        {"stale_copyright", "no_contact_on_homepage"},
    ),
]


@pytest.mark.parametrize(
    ("fixture", "present", "absent"),
    CASES,
    ids=[f"{fixture}:{'+'.join(sorted(present)) or 'none'}" for fixture, present, _ in CASES],
)
def test_each_fixture_page_produces_exactly_what_it_should(
    fixture: str, present: set[str], absent: set[str]
) -> None:
    produced = set(found(fixture))

    assert present <= produced, f"missing {sorted(present - produced)}; got {sorted(produced)}"
    assert not (absent & produced), f"must not report {sorted(absent & produced)}"


def test_every_new_finding_has_a_fixture_that_produces_it() -> None:
    new_codes = {
        "no_local_business_schema",
        "invalid_structured_data",
        "future_copyright",
        "default_title",
        "short_title",
        "long_title",
        "viewport_blocks_zoom",
        "site_builder",
        "multiple_h1",
        "placeholder_email",
        "placeholder_text",
        "no_click_to_call",
        # The comparison findings are covered by the matched pairs below.
    }
    covered = set().union(*(present for _, present, _ in CASES))

    assert new_codes <= covered, sorted(new_codes - covered)


# --- structured data: what is read -----------------------------------------------------------


def page_checks(fixture: str) -> Checks:
    return checks_for((PAGES / fixture).read_text(encoding="utf-8"))


def test_microdata_fields_are_extracted_including_the_nested_address() -> None:
    local = value_of(page_checks("sd_microdata.html"), "local_business")

    assert local["type"] == "Plumber"
    assert local["syntax"] == "microdata"
    assert local["name"] == "Ultimate Plumber"
    assert local["telephone"] == "+1-512-555-0100"
    assert local["address"] == {
        "street": "1234 Congress Ave Ste 303",
        "locality": "Austin",
        "region": "TX",
        "postal_code": "78701",
    }
    assert local["same_as"] == ["https://www.facebook.com/ultimateplumber"]
    assert local["opening_hours"] == ["Mo-Fr 08:00-17:00"]


def test_json_ld_fields_are_extracted_and_the_type_is_normalised() -> None:
    checks = page_checks("sd_json_ld_in_cdata.html")

    assert value_of(checks, "structured_data") == "Plumber", "not the full schema.org URL"
    assert value_of(checks, "local_business")["syntax"] == "json-ld"
    assert value_of(checks, "structured_data_errors")["count"] == 0


def test_a_broken_block_is_quoted_as_evidence_with_the_type_it_declares() -> None:
    checks = page_checks("sd_invalid_json_ld.html")
    errors = checks["structured_data_errors"]

    assert errors.value["count"] == 1
    assert errors.value["type_hint"] == "Plumber"
    assert '"telephone":"+1-512-555-0100",}' in (errors.evidence_text or "")


def test_schema_types_are_listed_when_none_is_a_local_business() -> None:
    checks = page_checks("sd_organization_only.html")

    assert value_of(checks, "structured_data") is False
    assert value_of(checks, "structured_data_types") == ["Organization", "WebSite"]


def test_a_real_subtype_outside_the_old_list_is_a_local_business() -> None:
    """`HardwareStore` was not in the 41-type list; calling it "not local" would be false."""
    html = '<script type="application/ld+json">{"@type": "HardwareStore"}</script>'

    assert value_of(checks_for(html), "structured_data") == "HardwareStore"


# --- the matched pairs: agree produces nothing, disagree produces the finding ---------------

NAP_PAGE = "nap_local_business.html"


def test_phone_agreeing_with_the_listing_produces_no_finding() -> None:
    assert "nap_phone_mismatch" not in found(NAP_PAGE)


def test_phone_disagreeing_with_the_listing_is_a_nap_finding() -> None:
    listing = replace(LISTING, phone_e164="+15125550199")

    assert "nap_phone_mismatch" in found(NAP_PAGE, listing=listing)


def test_the_phone_evidence_names_both_numbers_and_the_message_names_neither() -> None:
    listing = replace(LISTING, phone_e164="+15125550199")
    html = (PAGES / NAP_PAGE).read_text(encoding="utf-8")
    [finding] = [
        f
        for f in for_page(checks_for(html, listing=listing), None, context(listing))
        if f.code == "nap_phone_mismatch"
    ]

    assert "+1-512-555-0100" in (finding.evidence_text or "")
    assert "(512) 555-0199" in (finding.evidence_text or "")
    assert "555" not in finding.message, "messages reach the AI unscrubbed"


def test_a_tel_link_that_does_not_parse_is_not_compared() -> None:
    listing = replace(LISTING, phone_e164="+15125550199")
    checks = checks_for(
        (PAGES / "tel_unparseable.html").read_text(encoding="utf-8"), listing=listing
    )

    assert value_of(checks, "listing_comparison")["phone"]["status"] == "not_compared"
    assert "nap_phone_mismatch" not in codes(for_page(checks, None, context(listing)))


def test_a_listing_with_no_phone_is_not_compared() -> None:
    listing = replace(LISTING, phone_e164=None)

    assert "nap_phone_mismatch" not in found(NAP_PAGE, listing=listing)


def test_one_matching_number_among_several_is_agreement() -> None:
    """A second, call-tracking number beside the listed one is not a disagreement."""
    html = '<a href="tel:+1-512-555-0999">Call</a><a href="tel:(512) 555-0100">Office</a><h1>x</h1>'

    assert value_of(checks_for(html), "listing_comparison")["phone"]["status"] == "match"


def address_status(listing: Listing = LISTING) -> str:
    html = (PAGES / NAP_PAGE).read_text(encoding="utf-8")
    return str(
        value_of(checks_for(html, listing=listing), "listing_comparison")["address"]["status"]
    )


def test_the_address_comparison_is_data_and_never_a_finding() -> None:
    """Retired as a finding in v0.12.0 after two false accusations in production; the
    comparison stays in `listing_comparison` for a reviewer, and no listing makes a claim."""
    for listing in (
        LISTING,
        replace(LISTING, address_line2="Ste 303"),
        replace(LISTING, postal_code="78702"),
    ):
        assert "nap_address_mismatch" not in found(NAP_PAGE, listing=listing)


def test_address_agreeing_with_the_listing_is_recorded_as_a_match() -> None:
    """ "Ave" and "Avenue", "Suite #11" and "Suite 11" are the same address."""
    assert address_status() == "match"


def test_a_different_suite_is_recorded_as_a_mismatch_for_a_reviewer() -> None:
    assert address_status(replace(LISTING, address_line2="Ste 303")) == "mismatch"


def test_a_different_zip_is_recorded_as_a_mismatch_for_a_reviewer() -> None:
    assert address_status(replace(LISTING, postal_code="78702")) == "mismatch"


def test_a_street_name_spelled_differently_is_not_a_mismatch() -> None:
    """Street names are never compared: "N Congress Ave" and "Congress Avenue" may be one."""
    assert address_status(replace(LISTING, address_line1="1234 N Congress Avenue")) == "match"


def test_a_unit_on_one_side_only_is_not_a_mismatch() -> None:
    assert address_status(replace(LISTING, address_line2=None)) == "match"


def test_a_site_describing_several_locations_is_not_compared() -> None:
    node = (
        '{{"@type": "Plumber", "address": {{"streetAddress": "{street}", '
        '"postalCode": "{postal}"}}}}'
    )
    html = (
        '<script type="application/ld+json">['
        + node.format(street="99 Other St", postal="78745")
        + ","
        + node.format(street="1234 Congress Ave", postal="78701")
        + "]</script>"
    )
    comparison = value_of(checks_for(html), "listing_comparison")

    assert comparison["address"]["status"] == "not_compared"
    assert "2 businesses or locations" in comparison["address"]["reason"]


def test_a_one_line_address_takes_its_zip_from_the_end_not_the_street_number() -> None:
    """In "12345 Research Blvd, Austin, TX 78759" the first five digits are the street."""
    listing = replace(
        LISTING, address_line1="12345 Research Blvd", address_line2=None, postal_code="78759"
    )
    html = (
        '<script type="application/ld+json">{"@type": "Plumber", '
        '"address": "12345 Research Blvd, Austin, TX 78759"}</script>'
    )

    assert (
        value_of(checks_for(html, listing=listing), "listing_comparison")["address"]["status"]
        == "match"
    )


def test_a_listing_website_that_agrees_produces_no_finding() -> None:
    produced = found(NAP_PAGE)

    assert "listing_website_http" not in produced
    assert "listing_website_host_mismatch" not in produced


def test_a_listing_giving_http_for_an_https_site_is_a_finding() -> None:
    listing = replace(LISTING, website="http://ultimateplumber.test/")

    assert "listing_website_http" in found(NAP_PAGE, listing=listing)


def test_a_listing_on_www_for_a_site_that_names_itself_without_is_a_finding() -> None:
    listing = replace(LISTING, website="https://www.ultimateplumber.test/")

    produced = found(NAP_PAGE, listing=listing)

    assert "listing_website_host_mismatch" in produced
    assert "listing_website_http" not in produced


def test_a_listing_on_another_domain_is_not_compared() -> None:
    listing = replace(LISTING, website="https://ultimate-plumbing-austin.test/")

    produced = found(NAP_PAGE, listing=listing)

    assert "listing_website_host_mismatch" not in produced
    assert "listing_website_http" not in produced


# --- the business name in the title ----------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "name", "names_it"),
    [
        ("Ultimate Plumber | Austin", "Ultimate Plumber", True),
        ("Home | Business", "Ultimate Plumber", False),
        ("ATX Electrical Services", "ATX Electric LLC", True),
        ("Plumbing in Austin", "Barton Creek Plumbers", True),  # "plumb…" shared, lenient
        ("UltimatePlumber.com", "Ultimate Plumber", True),
        ("Welcome", "Smith & Sons", False),
        ("Anything", "The Company", None),  # no identifying word to look for
    ],
)
def test_the_title_is_checked_for_the_business_name_leniently(
    title: str, name: str, names_it: bool | None
) -> None:
    assert text_names_business(title, name) is names_it


def test_a_template_title_is_reported_even_when_it_shares_a_word_with_the_name() -> None:
    html = "<html><head><title>Home</title></head><body><h1>x</h1></body></html>"
    checks = checks_for(html, listing=replace(LISTING, name="Home Comfort HVAC"))

    produced = codes(for_page(checks, None, context(replace(LISTING, name="Home Comfort HVAC"))))

    assert "default_title" in produced


def test_placeholder_text_that_is_the_business_name_is_not_reported() -> None:
    listing = replace(LISTING, name="Your Company Store")

    assert "placeholder_text" not in found("placeholder_footer_text.html", listing=listing)
