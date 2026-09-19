"""What an audit of the Barton Creek demo site must say, read by a person.

A manual pass over one real audit (`bartoncreekplumbing.invalid`, the "old site" case)
turned up three ways the checks were technically true and practically misleading. Each one
is pinned here against the checked-in fixture, because none of them was a bug in a snippet
somebody wrote for a test — each only showed up in what a whole page produced together.

The three:

1. `tls_valid` read `true`, with the evidence "certificate verified", for a page served
   over plain **http**, where no certificate was ever presented;
2. presence checks disagreed with each other — `favicon` and `mailto_link` answered
   `false` for something absent while `booking`, `viewport_meta`, `meta_description`,
   `structured_data` and `ecommerce` answered `null` for exactly the same thing;
3. the `copyright_year` evidence was a window cut out of the HTML, so it began and ended
   mid-tag: `el:+1-512-555-0102">Call ... </footer`.
"""

import pathlib
from datetime import UTC, datetime

import pytest

from app.core.fetch_backends import demo_sites_root
from app.core.safe_fetch import FetchOutcome
from app.modules.audit_web.checks import (
    NOT_APPLICABLE_OVER_HTTP,
    Checks,
    analyse_html,
    fetch_checks,
    value_of,
)
from app.modules.audit_web.findings import FindingContext, codes, for_page
from app.modules.normalization.schemas import WebsiteKind

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
BARTON_CREEK = "bartoncreekplumbing.invalid"
BARTON_CREEK_URL = f"http://{BARTON_CREEK}/"
LONE_STAR = "lonestarplumbing.invalid"
LONE_STAR_URL = f"https://{LONE_STAR}/"


# The checks whose value answers "is this on the page?". On a page that was parsed, every
# one of them must be a captured value or `False` — never `null`.
PRESENCE_CHECKS = (
    "viewport_meta",
    "title",
    "meta_description",
    "favicon",
    "h1_present",
    "tel_link",
    "mailto_link",
    "contact_form",
    "booking",
    "ecommerce",
    "structured_data",
    "copyright_year",
    "js_shell_suspected",
)


def demo_page(host: str) -> str:
    return (pathlib.Path(demo_sites_root()) / host / "index.html").read_text(encoding="utf-8")


def audited(host: str, url: str) -> Checks:
    """Every check one demo homepage produces, fetch-level and HTML-level together."""
    body = demo_page(host)
    outcome = FetchOutcome(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=body.encode(),
        text=body,
    )
    checks = fetch_checks(outcome)
    checks.update(analyse_html(outcome, now=NOW)[0])
    return checks


@pytest.fixture
def barton_creek() -> Checks:
    return audited(BARTON_CREEK, BARTON_CREEK_URL)


# --- 1. no certificate means no verdict on one ------------------------------------------


def test_tls_valid_is_null_for_a_page_served_over_http(barton_creek: Checks) -> None:
    assert value_of(barton_creek, "https") is False
    assert value_of(barton_creek, "tls_valid") is None, (
        "no certificate was presented, so there is nothing to call valid"
    )
    assert barton_creek["tls_valid"].evidence_text == NOT_APPLICABLE_OVER_HTTP


def test_tls_valid_is_still_true_for_a_page_served_over_https() -> None:
    checks = audited(LONE_STAR, LONE_STAR_URL)

    assert value_of(checks, "tls_valid") is True
    assert "verified" in (checks["tls_valid"].evidence_text or "")


def test_an_http_page_produces_no_certificate_finding(barton_creek: Checks) -> None:
    """`tls_valid = null` must read as "not asked", never as "failed"."""
    context = FindingContext(
        industry="plumbing",
        website_kind=WebsiteKind.own_site,
        website=BARTON_CREEK_URL,
        booking_industries=frozenset({"plumbing"}),
        slow_mobile_score=50,
        stale_copyright_years=3,
        now=NOW,
    )

    found = codes(for_page(barton_creek, None, context))

    assert "no_https" in found
    assert "tls_invalid" not in found


# --- 2. one answer for "it is not there" ------------------------------------------------


@pytest.mark.parametrize("check", PRESENCE_CHECKS)
def test_a_parsed_page_answers_every_presence_check_true_or_false(
    barton_creek: Checks, check: str
) -> None:
    assert value_of(barton_creek, check) is not None, (
        f"{check} answered null on a page that was fetched and parsed; absent is false"
    )


def test_what_barton_creek_is_missing_all_reads_false(barton_creek: Checks) -> None:
    """The five that used to answer `null` here, beside the two that already said `false`."""
    for check in (
        "viewport_meta",
        "meta_description",
        "booking",
        "ecommerce",
        "structured_data",
        "favicon",
        "mailto_link",
    ):
        assert value_of(barton_creek, check) is False, check


def test_a_page_that_was_never_parsed_keeps_null() -> None:
    """The other half of the rule: `null` still means the check could not run."""
    unparsed = FetchOutcome(
        url=LONE_STAR_URL,
        final_url=LONE_STAR_URL,
        status_code=200,
        content_type="application/pdf",
        body=b"%PDF",
    )

    checks = fetch_checks(unparsed)
    checks.update(analyse_html(unparsed, now=NOW)[0])

    assert value_of(checks, "parsed") is False
    for check in PRESENCE_CHECKS:
        assert check not in checks, "a page we could not parse produces no content checks"
