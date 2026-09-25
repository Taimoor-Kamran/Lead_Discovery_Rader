"""The findings catalogue: how it is worded, and which checks turn into which finding.

The wording test walks the whole catalogue rather than a sample. A finding is read out to
a business owner, so "the audit saw X" is allowed and "your website is bad" never is —
and that has to hold for the entry somebody adds next year, not just today's.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.safe_fetch import FetchOutcome
from app.modules.audit_web import findings as findings_module
from app.modules.audit_web.checks import CheckResult, fetch_checks, html_checks
from app.modules.audit_web.findings import (
    ALLOWED_OPENINGS,
    BANNED_WORDS,
    CATALOGUE,
    FindingContext,
    codes,
    for_listing,
    for_missing_website,
    for_page,
    for_robots_blocked,
    for_unreachable,
)
from app.modules.normalization.schemas import WebsiteKind

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
URL = "https://example.test/"
BOOKING_INDUSTRIES = frozenset({"plumbing", "dental"})


def context(**overrides: Any) -> FindingContext:
    values: dict[str, Any] = {
        "industry": "plumbing",
        "website_kind": WebsiteKind.own_site,
        "website": URL,
        "booking_industries": BOOKING_INDUSTRIES,
        "slow_mobile_score": 50,
        "stale_copyright_years": 3,
        "now": NOW,
    }
    values.update(overrides)
    return FindingContext(**values)


def page(body: str, *, url: str = URL) -> dict[str, CheckResult]:
    outcome = FetchOutcome(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=body.encode(),
        text=body,
    )
    return {**fetch_checks(outcome), **html_checks(outcome, now=NOW)}


COMPLETE_PAGE = (
    "<html><head>"
    '<meta name="viewport" content="width=device-width">'
    "<title>Lone Star Plumbing</title>"
    '<meta name="description" content="Austin plumbers.">'
    '<link rel="icon" href="/f.ico">'
    '<script type="application/ld+json">{"@type":"Plumber","name":"Lone Star"}</script>'
    "</head><body><h1>Plumbing in Austin</h1>"
    "<h2>What we do</h2>"
    f"<p>{' '.join(['Leak repair, drain cleaning and water heaters across Austin.'] * 25)}</p>"
    '<a href="tel:+15125550100">Call</a>'
    '<a href="https://calendly.com/lonestar">Book now</a>'
    "<footer>&copy; 2026 Lone Star</footer>"
    '<script src="https://embed.tawk.to/lonestar/default" async></script>'
    "</body></html>"
)


# --- the wording rule -------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(CATALOGUE), ids=sorted(CATALOGUE))
def test_every_wording_template_is_an_observation_not_a_verdict(code: str) -> None:
    wording = CATALOGUE[code].wording

    assert wording.startswith(ALLOWED_OPENINGS), (
        f"'{code}' must open with one of {ALLOWED_OPENINGS}; it reads '{wording}'"
    )
    lowered = wording.lower()
    for banned in BANNED_WORDS:
        assert banned not in lowered, f"'{code}' must not say '{banned}': '{wording}'"
    assert wording.endswith("."), f"'{code}' should read as a sentence"


def test_every_catalogue_entry_is_keyed_by_its_own_code() -> None:
    for code, spec in CATALOGUE.items():
        assert spec.code == code


def test_the_catalogue_covers_every_code_the_spec_names() -> None:
    assert set(CATALOGUE) == {
        "no_website",
        "social_profile_only",
        "unreachable",
        "no_https",
        "tls_invalid",
        "builder_subdomain",
        "no_mobile_viewport",
        "missing_title",
        "missing_meta_description",
        "no_h1",
        "no_structured_data",
        "no_online_booking",
        "no_live_chat",
        "no_contact_on_homepage",
        "images_without_alt",
        "unlabelled_form_fields",
        "thin_content",
        "no_section_headings",
        "heading_level_skipped",
        "stale_copyright",
        "slow_mobile",
        "low_accessibility_score",
        "low_best_practices_score",
        "js_shell_suspected",
        "few_reviews",
        "robots_blocked",
    }


def test_only_no_website_may_omit_its_evidence_url() -> None:
    """Every other finding has to point at something a human can open."""
    produced = [
        *for_missing_website(
            context(website_kind=WebsiteKind.none, website=None), business_label="X"
        ),
        *for_missing_website(
            context(website_kind=WebsiteKind.social_profile, website="https://facebook.test/x"),
            business_label="X",
        ),
        *for_robots_blocked("robots.txt disallows us", f"{URL}robots.txt"),
        *for_unreachable(
            fetch_checks(FetchOutcome(url=URL, final_url=URL, error="ConnectError")), URL
        ),
        *for_page(page("<html><body></body></html>"), {"performance_score": 20}, context()),
        *for_listing(11, few_reviews=20),
    ]

    assert produced, "the sweep must actually produce findings"
    for finding in produced:
        assert finding.evidence_text, f"{finding.code} has no evidence text"
        # Both cite our own stored record, not a page: there is nothing to link to.
        if finding.code not in ("no_website", "few_reviews"):
            assert finding.evidence_url, f"{finding.code} has no evidence url"


# --- the no-fetch cases -----------------------------------------------------------------


def test_a_business_with_no_website_gets_one_finding_citing_its_record() -> None:
    produced = for_missing_website(
        context(website_kind=WebsiteKind.none, website=None), business_label="No Website Plumbing"
    )

    assert codes(produced) == ["no_website"]
    assert "No Website Plumbing" in (produced[0].evidence_text or "")
    assert produced[0].evidence_url is None


def test_a_business_with_only_a_social_profile_cites_that_profile() -> None:
    produced = for_missing_website(
        context(website_kind=WebsiteKind.social_profile, website="https://facebook.test/oak"),
        business_label="Oak Hill",
    )

    assert codes(produced) == ["social_profile_only"]
    assert produced[0].evidence_url == "https://facebook.test/oak"


# --- unreachable and robots -------------------------------------------------------------


def test_a_network_failure_is_reported_as_unreachable() -> None:
    checks = fetch_checks(FetchOutcome(url=URL, final_url=URL, error="ConnectError"))

    produced = for_unreachable(checks, URL)

    assert codes(produced) == ["unreachable"]
    assert URL in produced[0].message


def test_a_certificate_failure_is_reported_as_tls_invalid_rather_than_down() -> None:
    checks = fetch_checks(
        FetchOutcome(
            url=URL,
            final_url=URL,
            tls_valid=False,
            error="certificate verify failed",
            error_kind="tls",
        )
    )

    produced = for_unreachable(checks, URL)

    assert codes(produced) == ["tls_invalid"]


def test_robots_blocked_carries_the_reason_and_the_robots_url() -> None:
    produced = for_robots_blocked("robots.txt disallows LeadDiscoveryRadarBot", f"{URL}robots.txt")

    assert codes(produced) == ["robots_blocked"]
    assert produced[0].evidence_url == f"{URL}robots.txt"
    assert produced[0].service_category is None
    assert produced[0].severity.value == "info"


# --- the page rules ---------------------------------------------------------------------


def test_a_complete_page_produces_no_findings() -> None:
    assert for_page(page(COMPLETE_PAGE), {"performance_score": 92}, context()) == []


def test_an_empty_page_produces_every_content_finding() -> None:
    produced = for_page(page("<html><head></head><body></body></html>"), None, context())

    assert set(codes(produced)) == {
        "no_mobile_viewport",
        "missing_title",
        "missing_meta_description",
        "no_h1",
        "no_structured_data",
        "no_contact_on_homepage",
        "no_online_booking",
        "no_live_chat",
        "thin_content",
    }


def test_an_http_page_is_reported_as_no_https() -> None:
    produced = for_page(page(COMPLETE_PAGE, url="http://example.test/"), None, context())

    assert "no_https" in codes(produced)


def test_booking_is_only_expected_in_the_configured_industries() -> None:
    body = COMPLETE_PAGE.replace('<a href="https://calendly.com/lonestar">Book now</a>', "")

    assert "no_online_booking" in codes(for_page(page(body), None, context(industry="plumbing")))
    assert "no_online_booking" not in codes(
        for_page(page(body), None, context(industry="bookkeeping"))
    )
    assert "no_online_booking" not in codes(for_page(page(body), None, context(industry=None)))


def test_any_one_contact_option_is_enough() -> None:
    for contact in (
        '<a href="tel:+15125550100">Call</a>',
        '<a href="mailto:x@example.test">Email</a>',
        '<form><input type="email" name="from"></form>',
    ):
        produced = for_page(page(f"<html><body>{contact}</body></html>"), None, context())
        assert "no_contact_on_homepage" not in codes(produced)


@pytest.mark.parametrize(
    ("year", "expected"),
    [(2026, False), (2024, False), (2023, True), (2016, True)],
)
def test_a_copyright_year_three_years_behind_is_stale(year: int, expected: bool) -> None:
    body = f"<html><body><footer>&copy; {year} Someone</footer></body></html>"

    produced = codes(for_page(page(body), None, context()))

    assert ("stale_copyright" in produced) is expected


def test_the_stale_copyright_message_quotes_the_year() -> None:
    body = "<html><body><footer>&copy; 2016 Barton Creek</footer></body></html>"

    finding = next(f for f in for_page(page(body), None, context()) if f.code == "stale_copyright")

    assert "2016" in finding.message
    assert "2016" in (finding.evidence_text or "")


@pytest.mark.parametrize(("score", "expected"), [(92, False), (50, False), (49, True), (12, True)])
def test_a_low_pagespeed_score_is_reported_as_slow_mobile(score: int, expected: bool) -> None:
    produced = codes(for_page(page(COMPLETE_PAGE), {"performance_score": score}, context()))

    assert ("slow_mobile" in produced) is expected


def test_the_slow_mobile_finding_quotes_the_measured_numbers() -> None:
    psi = {
        "performance_score": 31,
        "lcp_ms": 5400,
        "cls": 0.24,
        "tbt_ms": 890,
        "crux_category": "SLOW",
    }

    finding = next(
        f for f in for_page(page(COMPLETE_PAGE), psi, context()) if f.code == "slow_mobile"
    )

    assert "31 out of 100" in finding.message
    evidence = finding.evidence_text or ""
    assert "LCP 5400 ms" in evidence
    assert "CLS 0.24" in evidence
    assert "CrUX SLOW" in evidence


def test_a_missing_pagespeed_result_produces_no_performance_finding() -> None:
    assert "slow_mobile" not in codes(for_page(page(COMPLETE_PAGE), None, context()))


def test_a_js_shell_page_is_flagged_as_possibly_incomplete() -> None:
    scripts = "".join(f'<script src="/a-{n}.js"></script>' for n in range(6))
    body = f'<html><body><div id="root"></div>{scripts}</body></html>'

    produced = codes(for_page(page(body), None, context()))

    assert "js_shell_suspected" in produced


def test_a_payload_round_trips_to_plain_json_types() -> None:
    produced = for_page(page("<html><body></body></html>"), {"performance_score": 10}, context())

    payload = findings_module.as_payload(produced)

    assert all(isinstance(item["code"], str) for item in payload)
    assert all(item["severity"] in {"info", "low", "medium", "high"} for item in payload)


# --- v0.11.0: booking calls to action, live chat, builder subdomains --------------------

# The shape of the real Copperhead Electrical homepage (fetched through `safe_fetch` on
# 2026-09-23): an Elementor button reading "BOOK A SERVICE" that links to the contact
# page, and no booking tool anywhere on the page. The markup is the button's structure,
# not the business's content; the host and the path are the demo's own.
ELEMENTOR_BOOK_A_SERVICE = (
    '<a class="elementor-button elementor-button-link elementor-size-sm" '
    'href="https://example.test/contact-us">'
    '<span class="elementor-button-content-wrapper">'
    '<span class="elementor-button-icon"><svg aria-hidden="true" '
    'class="e-font-icon-svg e-fas-chevron-right" viewbox="0 0 320 512"></svg></span>'
    '<span class="elementor-button-text">BOOK A SERVICE</span>'
    "</span></a>"
)


def with_body(body: str) -> str:
    return COMPLETE_PAGE.replace('<a href="https://calendly.com/lonestar">Book now</a>', body)


def test_booking_link_text_without_a_known_widget_is_not_reported_as_no_booking() -> None:
    """The Copperhead false positive: a call to action the audit must recognise."""
    checks = page(with_body(ELEMENTOR_BOOK_A_SERVICE))

    assert checks["booking"].value == "link text: BOOK A SERVICE"
    assert "elementor-button-text" in (checks["booking"].evidence_text or "")
    assert "no_online_booking" not in codes(for_page(checks, None, context()))


@pytest.mark.parametrize(
    "markup",
    [
        '<a href="/book-online"><img src="/cta.png" alt=""></a>',
        '<a href="https://example.test/services/schedule-service/">Plumbing</a>',
        '<a href="/request-service?from=home">Get started</a>',
        '<button aria-label="Book now"><svg></svg></button>',
        '<form action="/go"><input type="submit" value="Schedule service"></form>',
        '<div role="button" tabindex="0">Request service</div>',
        '<a href="/x">Book a service</a>',
        '<a href="/x">Schedule an appointment</a>',
    ],
)
def test_a_booking_call_to_action_is_recognised_by_text_label_or_path(markup: str) -> None:
    checks = page(with_body(markup))

    assert checks["booking"].value, markup
    assert "no_online_booking" not in codes(for_page(checks, None, context()))


@pytest.mark.parametrize(
    "markup",
    [
        '<a href="/books">Our favourite books</a>',
        '<a href="/bookkeeping">Bookkeeping</a>',
        '<a href="/contact-us">Contact us</a>',
        '<a href="mailto:book@example.test">Email</a>',
        '<input type="text" value="Book now">',
    ],
)
def test_links_that_are_not_booking_do_not_count(markup: str) -> None:
    checks = page(with_body(markup))

    assert checks["booking"].value is False, markup
    assert "no_online_booking" in codes(for_page(checks, None, context()))


def test_a_page_with_a_chat_widget_produces_no_live_chat_finding() -> None:
    checks = page(COMPLETE_PAGE)

    assert checks["live_chat"].value == "Tawk.to"
    assert "embed.tawk.to" in (checks["live_chat"].evidence_text or "")
    assert "no_live_chat" not in codes(for_page(checks, None, context()))


def test_a_page_without_a_chat_widget_produces_no_live_chat_with_its_evidence() -> None:
    checks = page(
        COMPLETE_PAGE.replace('<script src="https://embed.tawk.to', '<script src="https://x')
    )

    produced = [f for f in for_page(checks, None, context()) if f.code == "no_live_chat"]

    assert len(produced) == 1
    assert produced[0].message == ("Audit found no live chat or messaging widget on the homepage.")
    assert produced[0].evidence_url == URL


def test_a_wordpress_css_token_named_crisp_is_not_the_crisp_chat_widget() -> None:
    """Seen on a real WordPress homepage: `--wp--preset--shadow--crisp` is a shadow preset."""
    body = COMPLETE_PAGE.replace(
        '<script src="https://embed.tawk.to/lonestar/default" async></script>',
        "<style>body{--wp--preset--shadow--crisp: 6px 6px 0px rgb(0, 0, 0);}</style>",
    )

    assert page(body)["live_chat"].value is False


@pytest.mark.parametrize(
    "url",
    [
        "https://wixwaterworks.wixsite.com/home",
        "https://topelectricianaustin.wixstudio.com/",
        "https://somebody.square.site/",
    ],
)
def test_a_homepage_served_from_a_builder_subdomain_is_reported_with_its_host(url: str) -> None:
    produced = [
        f
        for f in for_page(page(COMPLETE_PAGE, url=url), None, context())
        if f.code == "builder_subdomain"
    ]

    assert len(produced) == 1
    assert produced[0].evidence_text == f"The homepage was served from {url.split('/')[2]}"
    assert produced[0].evidence_url == url


def test_the_same_builder_on_the_business_own_domain_is_not_a_builder_subdomain() -> None:
    """A Wix site on a custom domain is a different, weaker signal: no finding."""
    wix_on_own_domain = COMPLETE_PAGE.replace(
        "</head>", '<meta name="generator" content="Wix.com Website Builder"></head>'
    )
    checks = page(wix_on_own_domain, url="https://wixwaterworks-demo.com/")

    assert "Wix" in checks["tech_stack"].value["platforms"]
    assert "builder_subdomain" not in codes(for_page(checks, None, context()))


def test_a_builder_subdomain_that_redirects_to_its_own_domain_is_judged_by_where_it_landed() -> (
    None
):
    outcome = FetchOutcome(
        url="https://acme.wixsite.com/",
        final_url="https://acme-plumbing.test/",
        status_code=200,
        content_type="text/html",
        body=COMPLETE_PAGE.encode(),
        text=COMPLETE_PAGE,
        redirect_chain=("https://acme.wixsite.com/",),
    )
    checks = {**fetch_checks(outcome), **html_checks(outcome, now=NOW)}

    assert "builder_subdomain" not in codes(for_page(checks, None, context()))


# --- v0.11.0: page quality, counted in the document -------------------------------------


def only(code: str, body: str, **overrides: Any) -> list[Any]:
    return [f for f in for_page(page(body), None, context(**overrides)) if f.code == code]


def test_images_without_alt_are_counted_exactly_and_the_first_three_are_quoted() -> None:
    images = (
        '<img src="/a.jpg" alt="A van">'
        '<img src="/b.jpg" alt="">'  # decorative: correct markup, not counted
        '<img src="/c.jpg">'
        '<img src="/d.jpg" aria-hidden="true">'  # hidden from readers: not shown, not counted
        '<img src="/e.jpg" class="hero">'
        '<img src="/f.jpg">'
        '<img src="/g.jpg">'
    )
    body = with_body(images)
    checks = page(body)

    assert checks["images_without_alt"].value == {"images": 6, "without_alt": 4}
    [finding] = only("images_without_alt", body)
    assert finding.message == "Audit found 4 of 6 images with no alt text on the homepage."
    assert (
        finding.evidence_text
        == '<img src="/c.jpg"/> <img class="hero" src="/e.jpg"/> <img src="/f.jpg"/>'
    )


def test_a_page_whose_images_all_carry_alt_produces_no_alt_finding() -> None:
    assert only("images_without_alt", with_body('<img src="/a.jpg" alt="A van">')) == []


def test_unlabelled_form_fields_are_counted_and_placeholders_are_not_labels() -> None:
    form = (
        "<form>"
        '<label for="name">Name</label><input id="name" name="name">'
        '<label>Email <input type="email" name="email"></label>'
        '<input name="phone" aria-label="Phone">'
        '<textarea name="message" title="Message"></textarea>'
        '<input name="zip" placeholder="Zip code">'
        '<select name="service"><option>Leak</option></select>'
        '<input type="hidden" name="token" value="x">'
        '<input type="submit" value="Send">'
        "</form>"
    )
    body = with_body(form)

    assert page(body)["unlabelled_inputs"].value == {"fields": 6, "unlabelled": 2}
    [finding] = only("unlabelled_form_fields", body)
    assert finding.message == (
        "Audit found 2 form fields with no associated label on the homepage."
    )
    assert 'name="zip"' in (finding.evidence_text or "")
    assert 'name="service"' in (finding.evidence_text or "")


def test_one_unlabelled_field_is_worded_in_the_singular() -> None:
    [finding] = only("unlabelled_form_fields", with_body('<form><input name="q"></form>'))

    assert finding.message == "Audit found 1 form field with no associated label on the homepage."


def test_thin_content_reports_the_word_count_below_the_threshold() -> None:
    body = "<html><body><h1>Hi</h1><h2>Us</h2><p>" + " ".join(["word"] * 82) + "</p></body></html>"

    [finding] = only("thin_content", body)

    assert finding.message == "Audit found 84 words of visible text on the homepage."
    assert finding.evidence_text == "The homepage carries 84 words of visible text"
    assert only("thin_content", body, thin_content_words=80) == []


def test_a_javascript_shell_is_not_also_reported_as_thin_content() -> None:
    shell = "<html><body><div id=root></div>" + "<script src=/a.js></script>" * 6 + "</body></html>"

    produced = codes(for_page(page(shell), None, context()))

    assert "js_shell_suspected" in produced
    assert "thin_content" not in produced


def test_word_count_evidence_never_quotes_the_page() -> None:
    """A real header printed a named person's email; a count needs no quote."""
    checks = page(with_body("<p>Write to jane.doe@example.test today</p>"))

    assert "jane.doe" not in (checks["word_count"].evidence_text or "")


def test_a_main_heading_with_nothing_below_it_is_reported() -> None:
    body = COMPLETE_PAGE.replace("<h2>What we do</h2>", "")

    [finding] = only("no_section_headings", body)

    assert finding.message == "Audit found no section headings below the main heading."
    assert finding.evidence_text == "<h1>Plumbing in Austin</h1>"


def test_a_skipped_heading_level_is_reported_with_both_levels() -> None:
    body = COMPLETE_PAGE.replace("<h2>What we do</h2>", "<h4>What we do</h4>")

    [finding] = only("heading_level_skipped", body)

    assert finding.message == "Audit found the homepage headings skip a level, from h1 to h4."
    assert "<h4>What we do</h4>" in (finding.evidence_text or "")
    assert only("no_section_headings", body) == []


def test_going_back_up_a_level_is_not_a_skip_and_no_h1_is_left_to_no_h1() -> None:
    back_up = COMPLETE_PAGE.replace(
        "<h2>What we do</h2>", "<h2>What we do</h2><h3>Leaks</h3><h2>Where</h2>"
    )
    no_h1 = COMPLETE_PAGE.replace("<h1>Plumbing in Austin</h1>", "")

    assert only("heading_level_skipped", back_up) == []
    assert page(no_h1)["heading_structure"].value["sections_below_h1"] is None
    assert only("no_section_headings", no_h1) == []


# --- v0.11.0: PageSpeed accessibility and best practices --------------------------------


def scored(**scores: Any) -> dict[str, Any]:
    return {"performance_score": 92, "strategy": "mobile", **scores}


def test_scores_below_the_threshold_are_reported_in_pagespeed_terms() -> None:
    produced = {
        f.code: f
        for f in for_page(
            page(COMPLETE_PAGE), scored(accessibility_score=58, best_practices_score=67), context()
        )
    }

    assert produced["low_accessibility_score"].message == (
        "PageSpeed scored accessibility at 58 out of 100."
    )
    assert produced["low_best_practices_score"].message == (
        "PageSpeed scored best practices at 67 out of 100."
    )
    assert produced["low_accessibility_score"].evidence_text == (
        "PageSpeed Insights scored accessibility 58/100 (mobile)"
    )


def test_scores_at_or_above_the_threshold_and_null_scores_produce_nothing() -> None:
    for psi in (
        scored(accessibility_score=90, best_practices_score=100),
        scored(accessibility_score=None, best_practices_score=None),
        scored(),
    ):
        assert for_page(page(COMPLETE_PAGE), psi, context()) == [], psi


def test_the_quality_threshold_is_configurable() -> None:
    psi = scored(accessibility_score=85, best_practices_score=85)

    assert for_page(page(COMPLETE_PAGE), psi, context(quality_score_threshold=80)) == []
    assert len(for_page(page(COMPLETE_PAGE), psi, context())) == 2


# --- v0.11.0: the listing's review count ------------------------------------------------


def test_a_listing_with_few_reviews_is_reported_in_listing_terms() -> None:
    [finding] = for_listing(11, few_reviews=20)

    assert finding.code == "few_reviews"
    assert finding.message == "Listing shows 11 reviews."
    assert finding.evidence_text == "The business listing reports 11 user reviews"
    assert finding.severity.value == "low"
    assert for_listing(1, few_reviews=20)[0].message == "Listing shows 1 review."


def test_enough_reviews_or_an_unknown_count_produces_nothing() -> None:
    assert for_listing(20, few_reviews=20) == []
    assert for_listing(None, few_reviews=20) == []
    assert for_listing(0, few_reviews=20)[0].message == "Listing shows 0 reviews."


@pytest.mark.parametrize(
    ("value", "severity"),
    [(58, "medium"), (69, "medium"), (70, "low"), (88, "low"), (89, "low"), (90, None)],
)
def test_score_findings_are_graded_by_the_score(value: int, severity: str | None) -> None:
    produced = {
        f.code: f
        for f in for_page(
            page(COMPLETE_PAGE),
            scored(accessibility_score=value, best_practices_score=value),
            context(),
        )
    }

    for code in ("low_accessibility_score", "low_best_practices_score"):
        if severity is None:
            assert code not in produced
        else:
            assert produced[code].severity.value == severity


def test_the_medium_cut_is_configurable() -> None:
    [finding, _] = for_page(
        page(COMPLETE_PAGE),
        scored(accessibility_score=75, best_practices_score=75),
        context(quality_score_medium_below=80),
    )

    assert finding.severity.value == "medium"
