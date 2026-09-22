"""Reading the audit's `social_links` check back without inventing a link.

The check stores the platform keys as its value and `"key: href; key: href"` as its
evidence, clipped to 300 characters. The keys say which platforms the homepage links to;
the evidence is where the hrefs have to come from. A truncated or non-http href must leave
the platform named and unlinked rather than produce a link to the wrong place.
"""

from typing import Any

from app.modules.audit_web.checks import EVIDENCE_MAX_CHARS, CheckResult, as_payload
from app.modules.audit_web.models import WebsiteAudit
from app.modules.review import provenance

PAGE = "https://bartoncreekplumbing.invalid/"


def audit_with(check: dict[str, Any] | None) -> WebsiteAudit:
    """A WebsiteAudit that is never saved: `linked_profiles` only reads `checks`."""
    return WebsiteAudit(checks={} if check is None else {"social_links": check})


def social(value: Any, evidence_text: str | None, evidence_url: str | None = PAGE) -> WebsiteAudit:
    return audit_with(
        CheckResult(value, evidence_text=evidence_text, evidence_url=evidence_url).as_dict()
    )


def test_an_audit_that_found_no_links_yields_nothing() -> None:
    found = provenance.linked_profiles(social([], "No social profile links on the homepage"))

    assert found.profiles == []
    assert found.page_url is None


def test_no_audit_at_all_yields_nothing() -> None:
    assert provenance.linked_profiles(None).profiles == []


def test_a_missing_check_yields_nothing() -> None:
    """A page that was not parsed carries no checks; that is not "no links found"."""
    assert provenance.linked_profiles(audit_with(None)).profiles == []


def test_each_platform_is_named_and_linked_from_the_evidence() -> None:
    found = provenance.linked_profiles(
        social(
            ["facebook", "instagram"],
            "facebook: https://www.facebook.invalid/barton; "
            "instagram: https://www.instagram.invalid/barton",
        )
    )

    assert [(p.platform, p.url) for p in found.profiles] == [
        ("facebook", "https://www.facebook.invalid/barton"),
        ("instagram", "https://www.instagram.invalid/barton"),
    ]
    # The page the links were read on, so the screen can attribute them to the business.
    assert found.page_url == PAGE


def test_a_platform_whose_link_is_not_recoverable_is_still_named() -> None:
    """The check's value is authoritative; a missing href is `null`, never a guess."""
    found = provenance.linked_profiles(
        social(["facebook", "yelp"], "facebook: https://www.facebook.invalid/barton")
    )

    assert [(p.platform, p.url) for p in found.profiles] == [
        ("facebook", "https://www.facebook.invalid/barton"),
        ("yelp", None),
    ]


def test_a_non_http_href_is_not_offered_as_a_link() -> None:
    found = provenance.linked_profiles(social(["facebook"], "facebook: javascript:alert(1)"))

    assert found.profiles[0].url is None


def test_the_last_href_of_clipped_evidence_is_dropped_rather_than_truncated() -> None:
    """Evidence is cut at 300 characters, so the final href may be half a URL.

    Rendering half a URL would be a working link to somewhere we never saw. The platform is
    still named from the check's value; only its link is withheld.
    """
    facebook = f"https://www.facebook.invalid/{'a' * 100}"
    instagram = f"https://www.instagram.invalid/{'b' * 200}"
    evidence = f"facebook: {facebook}; instagram: {instagram}"[:EVIDENCE_MAX_CHARS]
    assert len(evidence) == EVIDENCE_MAX_CHARS
    assert facebook in evidence and instagram not in evidence, "only the last href is cut"

    found = provenance.linked_profiles(social(["facebook", "instagram"], evidence))

    by_platform = {p.platform: p.url for p in found.profiles}
    assert by_platform["facebook"] == facebook, "a complete href is still offered"
    assert by_platform["instagram"] is None, "a clipped href is withheld, not truncated"


def test_a_single_clipped_href_is_withheld_too() -> None:
    """One very long link, cut: there is no complete pair, so nothing is linked."""
    evidence = f"facebook: https://www.facebook.invalid/{'a' * 400}"[:EVIDENCE_MAX_CHARS]

    found = provenance.linked_profiles(social(["facebook"], evidence))

    assert [(p.platform, p.url) for p in found.profiles] == [("facebook", None)]


def test_the_page_url_is_only_offered_when_it_is_openable() -> None:
    assert (
        provenance.linked_profiles(
            social(["facebook"], "facebook: https://x.invalid/", evidence_url="not a url")
        ).page_url
        is None
    )


def test_the_real_check_payload_round_trips() -> None:
    """Built from `as_payload`, the way the audit writes it, not from a hand-made dict."""
    payload = as_payload(
        {
            "social_links": CheckResult(
                ["facebook"],
                evidence_text="facebook: https://www.facebook.invalid/barton",
                evidence_url=PAGE,
            )
        }
    )

    found = provenance.linked_profiles(WebsiteAudit(checks=payload))

    assert [(p.platform, p.url) for p in found.profiles] == [
        ("facebook", "https://www.facebook.invalid/barton")
    ]
