"""The four false findings the production canary found, each pinned by the case it came from.

On 2026-09-28 the first real re-audit (Plumbing in Austin, 18 businesses) produced:

* Proven Plumbing & Air — `nap_address_mismatch` between "Suite 402" and a listing stored
  as "Suite Suite 402": the unit parser read the second "Suite" as the unit;
* Clarke Kent Plumbing — `placeholder_text` "your company", from a customer testimonial
  printed in the footer;
* AAA Auger, Mr. Rooter, Fox Service, Rooter-Man — every finding read off a Cloudflare
  bot-protection page (HTTP 403) instead of the homepage;
* 1st Home & Commercial — `site_builder` Squarespace, evidenced only by an og:image URL on
  Squarespace's CDN.

Run 1 of the full re-audit then found a second false address claim — DC Electric, "Suite 204
AB" against "Suite 204AB" — after which `nap_address_mismatch` stopped being a finding at all;
the comparison is kept as data.

Each fixture below reproduces the real strings, and each test fails on the code the canary
ran. Where a fix narrows a rule, a matched test proves the rule still fires on the real thing.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.core.safe_fetch import FetchOutcome
from app.modules.adapters.base import AddressPart
from app.modules.audit_web import checks as checks_module
from app.modules.audit_web.checks import CheckResult
from app.modules.audit_web.findings import CATALOGUE, Severity, codes, for_page
from app.modules.audit_web.listing import Listing
from app.modules.normalization.addresses import from_components
from tests.unit.test_finished_sentences import checks_for, context

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def outcome(name: str, status: int) -> FetchOutcome:
    body = page(name)
    return FetchOutcome(
        url="https://www.example-plumber.test/",
        final_url="https://www.example-plumber.test/",
        status_code=status,
        content_type="text/html; charset=UTF-8",
        body=body.encode(),
        text=body,
    )


def challenge(name: str, status: int) -> CheckResult | None:
    # Looked up by name so this module still imports on the code the canary ran.
    detect = getattr(checks_module, "bot_challenge")  # noqa: B009
    result: CheckResult | None = detect(outcome(name, status))
    return result


# --- Cloudflare challenge pages ----------------------------------------------------------


def test_the_managed_challenge_is_recognised_as_cloudflare() -> None:
    """AAA Auger and Mr. Rooter: HTTP 403, "Just a moment..."."""
    result = challenge("canary_cloudflare_just_a_moment.html", 403)

    assert result is not None
    assert result.value == "Cloudflare"
    assert "HTTP 403" in (result.evidence_text or "")
    assert "Just a moment..." in (result.evidence_text or "")


def test_the_block_page_is_recognised_as_cloudflare() -> None:
    """Fox Service and Rooter-Man: HTTP 403, "Attention Required! | Cloudflare"."""
    result = challenge("canary_cloudflare_attention_required.html", 403)

    assert result is not None
    assert result.value == "Cloudflare"


def test_a_challenge_served_with_503_is_still_a_challenge() -> None:
    assert challenge("canary_cloudflare_just_a_moment.html", 503) is not None


def test_a_real_homepage_carrying_cloudflares_script_is_not_a_challenge() -> None:
    """Cloudflare injects `/cdn-cgi/challenge-platform/` into ordinary 200 pages too."""
    assert challenge("canary_cloudflare_script_on_real_homepage.html", 200) is None


def test_a_plain_403_without_a_vendor_mark_is_not_called_a_challenge() -> None:
    assert challenge("canary_proven_plumbing.html", 403) is None


# --- Proven Plumbing & Air: "Suite 402" and "Suite Suite 402" ----------------------------

PROVEN = Listing(
    name="Proven Plumbing & Air",
    phone_e164="+15125550142",
    address_line1="300 Brushy Creek Road",
    address_line2="Suite Suite 402",  # exactly as the production record stores it
    city="Cedar Park",
    state="TX",
    postal_code="78613",
    country="US",
    website="https://provenplumbing.test/",
)


def found(name: str, listing: Listing) -> list[str]:
    return codes(for_page(checks_for(page(name), listing=listing), None, context(listing)))


def address_status(name: str, listing: Listing) -> str:
    comparison = checks_module.value_of(
        checks_for(page(name), listing=listing), "listing_comparison"
    )
    return str(comparison["address"]["status"])


def test_the_same_suite_written_twice_over_is_a_match() -> None:
    assert address_status("canary_proven_plumbing.html", PROVEN) == "match"


def test_a_really_different_suite_is_still_recorded_as_a_mismatch() -> None:
    listing = replace(PROVEN, address_line2="Suite Suite 403")

    assert address_status("canary_proven_plumbing.html", listing) == "mismatch"


# DC Electric, production run 1: the site writes "Suite 204 AB", the listing "Suite 204AB".
DC_ELECTRIC = replace(
    PROVEN,
    name="DC Electric",
    address_line1="3906 North Lamar Boulevard",
    address_line2="Suite 204AB",
    city="Austin",
    postal_code="78756",
)


def test_a_unit_letter_written_apart_is_the_same_suite() -> None:
    assert address_status("canary_dc_electric.html", DC_ELECTRIC) == "match"


def test_no_address_comparison_ever_becomes_a_finding() -> None:
    """Retired as a finding after run 1: two claims in 36 businesses, both false."""
    different = replace(DC_ELECTRIC, address_line2="Suite 310", postal_code="78701")

    assert address_status("canary_dc_electric.html", different) == "mismatch"
    for listing in (DC_ELECTRIC, different):
        assert "nap_address_mismatch" not in found("canary_dc_electric.html", listing)


def test_a_places_unit_that_names_its_designator_is_not_prefixed_again() -> None:
    """Where "Suite Suite 402" came from: `Suite ` was put in front of "Suite 402"."""

    def line2(subpremise: str) -> str | None:
        part = AddressPart(long_text=subpremise, short_text=subpremise, types=("subpremise",))
        return from_components([part]).line2

    assert line2("Suite 402") == "Suite 402"
    assert line2("Ste 11") == "Ste 11"
    assert line2("#4") == "#4"
    assert line2("402") == "Suite 402"


# --- Clarke Kent Plumbing: a testimonial is not a placeholder ----------------------------

CLARKE_KENT = replace(PROVEN, name="Clarke Kent Plumbing", address_line2=None)


def test_a_footer_testimonial_saying_your_company_is_not_placeholder_text() -> None:
    assert "placeholder_text" not in found("canary_clarke_kent_footer.html", CLARKE_KENT)


def test_a_placeholder_on_the_copyright_line_still_fires() -> None:
    """Ultimate Plumber's real footer: "© 2035 by Business Name. Powered and secured by Wix"."""
    listing = replace(PROVEN, name="Ultimate Plumber", address_line2=None)

    assert "placeholder_text" in found("ultimate_plumber.html", listing)


# --- 1st Home & Commercial: an asset URL is not a builder --------------------------------


def test_an_og_image_on_a_builders_cdn_is_not_a_builder_finding() -> None:
    assert "site_builder" not in found("canary_squarespace_og_image_only.html", PROVEN)


def test_the_builders_own_script_and_classes_are() -> None:
    html = page("canary_squarespace_rendered.html")
    produced = for_page(checks_for(html, listing=PROVEN), None, context(PROVEN))
    [finding] = [f for f in produced if f.code == "site_builder"]

    assert "Squarespace" in finding.message
    assert "assets.squarespace.com" in (finding.evidence_text or "")


# --- the phone comparison: kept, but low and honest --------------------------------------


def test_a_phone_difference_is_low_and_names_call_tracking() -> None:
    spec = CATALOGUE["nap_phone_mismatch"]

    assert spec.severity is Severity.low
    assert "call-tracking" in spec.wording
    assert "wrong" not in spec.wording and "incorrect" not in spec.wording
