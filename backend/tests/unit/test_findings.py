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
    '<a href="tel:+15125550100">Call</a>'
    '<a href="https://calendly.com/lonestar">Book now</a>'
    "<footer>&copy; 2026 Lone Star</footer>"
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
        "no_mobile_viewport",
        "missing_title",
        "missing_meta_description",
        "no_h1",
        "no_structured_data",
        "no_online_booking",
        "no_contact_on_homepage",
        "stale_copyright",
        "slow_mobile",
        "js_shell_suspected",
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
    ]

    assert produced, "the sweep must actually produce findings"
    for finding in produced:
        assert finding.evidence_text, f"{finding.code} has no evidence text"
        if finding.code != "no_website":
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
