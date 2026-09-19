"""Every deterministic check, against a hand-written HTML snippet.

Each test states what a page says and what the audit must therefore record — including
the evidence, because a finding without its verbatim source is exactly what this spec
forbids.
"""

from datetime import UTC, datetime

import pytest
from bs4 import BeautifulSoup

from app.core.safe_fetch import FetchOutcome
from app.modules.audit_web.checks import (
    fetch_checks,
    html_checks,
    page_text,
    value_of,
    visible_text,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
URL = "https://example.test/"


def outcome(
    body: str,
    *,
    url: str = URL,
    final_url: str | None = None,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
) -> FetchOutcome:
    return FetchOutcome(
        url=url,
        final_url=final_url or url,
        status_code=status,
        content_type=content_type,
        body=body.encode(),
        text=body,
    )


def checks_for(body: str, **kwargs: object) -> dict[str, object]:
    result = html_checks(outcome(body, **kwargs), now=NOW)  # type: ignore[arg-type]
    return {key: value.value for key, value in result.items()}


# --- from the fetch --------------------------------------------------------------------


def test_a_successful_fetch_records_status_url_and_https() -> None:
    checks = fetch_checks(outcome("<html></html>"))

    assert value_of(checks, "reachable") is True
    assert value_of(checks, "http_status") == 200
    assert value_of(checks, "final_url") == URL
    assert value_of(checks, "https") is True
    assert value_of(checks, "tls_valid") is True
    assert checks["http_status"].evidence_url == URL


def test_an_http_site_is_recorded_as_not_https() -> None:
    checks = fetch_checks(outcome("<html></html>", url="http://example.test/"))

    assert value_of(checks, "https") is False
    assert "http://example.test/" in (checks["https"].evidence_text or "")


def test_a_redirect_from_http_to_https_is_recorded_as_such() -> None:
    result = FetchOutcome(
        url="http://example.test/",
        final_url="https://example.test/",
        status_code=200,
        redirect_chain=("http://example.test/",),
        content_type="text/html",
        body=b"<html></html>",
        text="<html></html>",
    )

    checks = fetch_checks(result)

    assert value_of(checks, "https") is True
    assert value_of(checks, "http_redirects_to_https") is True
    assert value_of(checks, "redirect_chain") == ["http://example.test/"]


def test_an_unreachable_site_records_why() -> None:
    result = FetchOutcome(url=URL, final_url=URL, error="ConnectError", error_kind="connect")

    checks = fetch_checks(result)

    assert value_of(checks, "reachable") is False
    assert value_of(checks, "http_status") is None
    assert "ConnectError" in (checks["reachable"].evidence_text or "")


def test_a_tls_failure_records_the_certificate_outcome() -> None:
    result = FetchOutcome(
        url=URL, final_url=URL, tls_valid=False, error="certificate verify failed", error_kind="tls"
    )

    checks = fetch_checks(result)

    assert value_of(checks, "tls_valid") is False
    assert "did not verify" in (checks["tls_valid"].evidence_text or "")


def test_a_non_html_body_is_recorded_but_produces_no_html_checks() -> None:
    result = FetchOutcome(
        url=URL, final_url=URL, status_code=200, content_type="application/pdf", body=b"%PDF"
    )

    assert value_of(fetch_checks(result), "parsed") is False
    assert html_checks(result) == {}


def test_a_truncated_body_is_flagged() -> None:
    result = FetchOutcome(
        url=URL,
        final_url=URL,
        status_code=200,
        content_type="text/html",
        body=b"<html>",
        text="<html>",
        truncated=True,
    )

    assert value_of(fetch_checks(result), "truncated") is True


# --- meta and content ------------------------------------------------------------------


def test_the_viewport_title_and_description_are_captured_with_their_tags() -> None:
    body = (
        "<html><head>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Lone Star Plumbing</title>"
        '<meta name="description" content="Austin plumbers since 1998.">'
        "</head><body><h1>Plumbing</h1></body></html>"
    )
    result = html_checks(outcome(body), now=NOW)

    assert result["viewport_meta"].value == "width=device-width, initial-scale=1"
    assert "meta" in (result["viewport_meta"].evidence_text or "")
    assert result["title"].value == "Lone Star Plumbing"
    assert result["title_length"].value == len("Lone Star Plumbing")
    assert result["meta_description"].value == "Austin plumbers since 1998."
    assert result["meta_description_length"].value == 27
    assert result["h1_present"].value is True


def test_a_missing_viewport_description_and_h1_are_null_or_false() -> None:
    result = html_checks(outcome("<html><head></head><body><p>Hi</p></body></html>"), now=NOW)

    assert result["viewport_meta"].value is None
    assert "No " in (result["viewport_meta"].evidence_text or "")
    assert result["title"].value is None
    assert result["meta_description"].value is None
    assert result["h1_present"].value is False


def test_an_empty_title_counts_as_missing() -> None:
    assert checks_for("<html><head><title>  </title></head><body></body></html>")["title"] is None


def test_an_h1_with_no_text_does_not_count() -> None:
    assert checks_for("<html><body><h1><img src='x.png'></h1></body></html>")["h1_present"] is False


def test_a_favicon_link_is_detected() -> None:
    assert (
        checks_for('<html><head><link rel="icon" href="/f.ico"></head></html>')["favicon"] is True
    )
    assert checks_for("<html><head></head></html>")["favicon"] is False


# --- contact options -------------------------------------------------------------------


def test_a_tel_link_is_recorded_as_presence_plus_one_example() -> None:
    body = (
        "<html><body>"
        '<a href="tel:+15125550100">Call us</a>'
        '<a href="tel:+15125550101">Second line</a>'
        "</body></html>"
    )
    result = html_checks(outcome(body), now=NOW)

    assert result["tel_link"].value is True
    evidence = result["tel_link"].evidence_text or ""
    assert "+15125550100" in evidence
    assert "+15125550101" not in evidence, "one example only; this system harvests nothing"


def test_a_mailto_link_is_detected() -> None:
    body = '<html><body><a href="mailto:hello@example.test">Email</a></body></html>'

    assert checks_for(body)["mailto_link"] is True
    assert checks_for("<html><body></body></html>")["mailto_link"] is False


@pytest.mark.parametrize(
    "form",
    [
        '<form><input type="email" name="from"><button>Send</button></form>',
        '<form><input type="text" name="your-email"></form>',
        '<form><input type="text" name="phone"></form>',
        '<form><input type="text" id="tel" placeholder="Telephone"></form>',
    ],
)
def test_a_form_with_a_contact_field_counts_as_a_contact_form(form: str) -> None:
    assert checks_for(f"<html><body>{form}</body></html>")["contact_form"] is True


def test_a_search_form_is_not_a_contact_form() -> None:
    body = '<html><body><form><input type="search" name="q"></form></body></html>'

    assert checks_for(body)["contact_form"] is False


# --- booking ----------------------------------------------------------------------------


def test_a_booking_widget_is_named() -> None:
    body = '<html><body><script src="https://assets.calendly.com/x.js"></script></body></html>'

    result = html_checks(outcome(body), now=NOW)

    assert result["booking"].value == "Calendly"
    assert "calendly.com" in (result["booking"].evidence_text or "")


def test_a_book_online_link_counts_as_booking() -> None:
    body = '<html><body><a href="/appointments">Book Now</a></body></html>'

    result = html_checks(outcome(body), now=NOW)

    assert result["booking"].value == "link text: Book Now"


def test_a_page_with_no_booking_says_so_without_guessing() -> None:
    result = html_checks(outcome("<html><body><a href='/about'>About</a></body></html>"), now=NOW)

    assert result["booking"].value is None
    assert "No known booking widget" in (result["booking"].evidence_text or "")


# --- e-commerce, social, structured data, tech ------------------------------------------


def test_a_shop_platform_is_named() -> None:
    body = '<html><body><script src="https://cdn.shopify.com/s/x.js"></script></body></html>'

    assert checks_for(body)["ecommerce"] == "Shopify"


def test_a_cart_link_alone_counts_as_ecommerce() -> None:
    body = '<html><body><a href="/cart">Basket (2)</a></body></html>'

    assert checks_for(body)["ecommerce"] == "cart link"


def test_social_links_are_listed_by_platform() -> None:
    body = (
        "<html><body>"
        '<a href="https://www.facebook.com/x">FB</a>'
        '<a href="https://instagram.com/x">IG</a>'
        '<a href="https://x.com/x">X</a>'
        '<a href="https://www.yelp.com/biz/x">Yelp</a>'
        "</body></html>"
    )
    result = html_checks(outcome(body), now=NOW)

    assert result["social_links"].value == ["facebook", "instagram", "x", "yelp"]
    assert "facebook.com/x" in (result["social_links"].evidence_text or "")


def test_local_business_json_ld_is_detected_including_inside_a_graph() -> None:
    body = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":[{"@type":"WebSite"},{"@type":"Plumber",'
        '"name":"Lone Star"}]}'
        "</script></head><body></body></html>"
    )
    result = html_checks(outcome(body), now=NOW)

    assert result["structured_data"].value == "Plumber"
    assert "Lone Star" in (result["structured_data"].evidence_text or "")


def test_json_ld_that_is_not_a_local_business_is_not_counted() -> None:
    body = (
        '<html><head><script type="application/ld+json">{"@type":"Organization"}</script>'
        "</head></html>"
    )

    assert checks_for(body)["structured_data"] is None


def test_broken_json_ld_is_ignored_rather_than_crashing() -> None:
    body = '<html><head><script type="application/ld+json">{nope}</script></head></html>'

    assert checks_for(body)["structured_data"] is None


def test_the_tech_stack_reads_the_generator_and_the_signatures() -> None:
    body = (
        '<html><head><meta name="generator" content="WordPress 6.5.2">'
        '<link href="/wp-content/themes/x/style.css" rel="stylesheet"></head></html>'
    )
    result = html_checks(outcome(body), now=NOW)

    assert result["tech_stack"].value == {
        "generator": "WordPress 6.5.2",
        "platforms": ["WordPress"],
    }


def test_a_page_with_no_platform_signature_reports_an_empty_stack() -> None:
    assert checks_for("<html><body>Plain</body></html>")["tech_stack"] == {
        "generator": None,
        "platforms": [],
    }


# --- copyright year ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("footer", "expected"),
    [
        ("&copy; 2016 Barton Creek Plumbing", 2016),
        ("Copyright 2024 Lone Star", 2024),
        ("© 2018-2023 Some Firm", 2023),
        ("(c) 2019 Another", 2019),
        ("Serving Austin since 1998", None),
        ("© 1899 Ancient", None),
        ("© 2099 The Future", None),
    ],
)
def test_the_copyright_year_is_the_highest_believable_one(
    footer: str, expected: int | None
) -> None:
    body = f"<html><body><footer>{footer}</footer></body></html>"

    assert checks_for(body)["copyright_year"] == expected


def test_the_newest_copyright_year_wins_when_several_appear() -> None:
    body = "<html><body><p>© 2012 Old</p><footer>© 2025 New</footer></body></html>"

    assert checks_for(body)["copyright_year"] == 2025


# --- JavaScript shell -------------------------------------------------------------------


def test_a_page_with_no_text_and_many_scripts_is_flagged_as_a_js_shell() -> None:
    scripts = "".join(f'<script src="/app-{n}.js"></script>' for n in range(6))
    body = f'<html><body><div id="root"></div>{scripts}</body></html>'

    result = html_checks(outcome(body), now=NOW)

    assert result["js_shell_suspected"].value is True
    assert "6 script tags" in (result["js_shell_suspected"].evidence_text or "")


def test_a_normal_page_with_scripts_is_not_flagged() -> None:
    scripts = "".join(f'<script src="/app-{n}.js"></script>' for n in range(6))
    body = f"<html><body><p>{'Real content about plumbing. ' * 20}</p>{scripts}</body></html>"

    assert checks_for(body)["js_shell_suspected"] is False


# --- page text --------------------------------------------------------------------------


def test_visible_text_leaves_out_scripts_and_styles() -> None:
    soup = BeautifulSoup(
        "<html><body><style>p{color:red}</style><p>Hello</p><script>var x=1</script></body></html>",
        "lxml",
    )

    assert visible_text(soup) == "Hello"


def test_page_text_is_capped() -> None:
    body = f"<html><body><p>{'word ' * 500}</p></body></html>"

    text = page_text(outcome(body), limit=50)

    assert text is not None
    assert len(text) == 50


def test_page_text_is_none_when_nothing_was_parsed() -> None:
    result = FetchOutcome(url=URL, final_url=URL, status_code=200, content_type="application/pdf")

    assert page_text(result, limit=100) is None


def test_evidence_is_clipped_to_the_limit() -> None:
    body = f"<html><head><title>{'t' * 500}</title></head></html>"

    evidence = html_checks(outcome(body), now=NOW)["title"].as_dict()["evidence_text"]

    assert evidence is not None
    assert len(evidence) == 300
