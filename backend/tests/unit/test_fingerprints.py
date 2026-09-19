"""Every signature in `fingerprints.py` is matched by a snippet that contains it.

Parametrised over the catalogue itself, so adding a signature without a working pattern
fails here rather than quietly never matching anything in production.
"""

import pytest

from app.modules.audit_web.fingerprints import (
    ALL_SIGNATURE_GROUPS,
    BOOKING_SIGNATURES,
    ECOMMERCE_SIGNATURES,
    TECH_SIGNATURES,
    Signature,
    booking_text_match,
    find_signatures,
    generator_label,
    is_local_business_type,
    snippet_around,
    snippet_forward,
    trim_to_words,
)

ALL_SIGNATURES: list[tuple[str, Signature]] = [
    (group, signature) for group, signatures in ALL_SIGNATURE_GROUPS for signature in signatures
]


@pytest.mark.parametrize(
    ("group", "signature"),
    ALL_SIGNATURES,
    ids=[f"{group}:{signature.key}" for group, signature in ALL_SIGNATURES],
)
def test_every_signature_matches_a_snippet_containing_it(group: str, signature: Signature) -> None:
    assert signature.patterns, "a signature with no pattern can never match"
    for pattern in signature.patterns:
        assert pattern == pattern.lower(), "patterns are matched against lowercased HTML"
        page = f'<html><body><script src="https://example.test/{pattern}"></script></body></html>'
        hits = find_signatures(page.lower(), [signature])

        assert [hit.key for hit in hits] == [signature.key]
        assert hits[0].label == signature.label
        assert pattern in hits[0].evidence


def test_a_signature_is_reported_once_even_with_several_patterns_present() -> None:
    page = "cdn.shopify.com and myshopify.com both appear"

    hits = find_signatures(page, ECOMMERCE_SIGNATURES)

    assert [hit.key for hit in hits] == ["shopify"]


def test_a_page_with_no_signature_matches_nothing() -> None:
    assert find_signatures("<html><body>Just words</body></html>", BOOKING_SIGNATURES) == []


def test_the_evidence_is_the_verbatim_text_around_the_hit() -> None:
    page = "before " * 40 + "calendly.com" + " after" * 40

    hits = find_signatures(page, BOOKING_SIGNATURES)

    assert "calendly.com" in hits[0].evidence
    assert hits[0].evidence in " ".join(page.split())


def test_snippet_around_never_runs_past_the_page() -> None:
    assert snippet_around("short", 0, 5) == "short"


def test_signature_evidence_is_the_whole_tag_the_hit_sits_in() -> None:
    """A window cut blind reads as `r" content="wordpress 6.5.2">`. The tag reads as HTML."""
    page = (
        "<html><head><title>a plumber in austin</title>"
        '<meta name="generator" content="wordpress 6.5.2">'
        '<link rel="stylesheet" href="/wp-content/themes/plumber/style.css">'
        "</head><body><h1>zilker pipe works</h1></body></html>"
    )

    hits = find_signatures(page, TECH_SIGNATURES)

    assert [hit.label for hit in hits] == ["WordPress"]
    assert hits[0].evidence == '<link rel="stylesheet" href="/wp-content/themes/plumber/style.css">'


def test_signature_evidence_in_the_page_text_is_trimmed_to_whole_words() -> None:
    """A signature that is not inside a tag still may not be shown with half a word."""
    page = "built by a local agency " * 6 + "joomla! " + "and rebuilt every few years " * 6

    hits = find_signatures(page, TECH_SIGNATURES)

    evidence = hits[0].evidence
    assert "joomla!" in evidence
    collapsed = " ".join(page.split())
    assert f" {evidence} " in f" {collapsed} ", "the snippet starts and ends on a word"


def test_trim_to_words_drops_only_the_words_it_cut_open() -> None:
    text = "the quick brown fox"

    assert trim_to_words(text, 5, 15) == "brown"
    assert trim_to_words(text, 4, 15, trim_start=False) == "quick brown"
    assert trim_to_words(text, 0, len(text)) == text


def test_snippet_forward_keeps_its_first_character() -> None:
    """The copyright mark is the point of the snippet, so it is never trimmed away."""
    text = "Site by Someone \u00a92016 Barton Creek Plumbing LLC. All rights reserved."
    mark = text.index("\u00a9")

    assert snippet_forward(text, mark, 200) == text[mark:]
    assert snippet_forward(text, mark, 30) == "\u00a92016 Barton Creek Plumbing"


@pytest.mark.parametrize(
    "text",
    [
        "book now",
        "book online",
        "book an appointment",
        "schedule online",
        "schedule a visit",
        "request a quote",
        "request quote",
        "book a consultation",
    ],
)
def test_booking_call_to_action_text_is_recognised(text: str) -> None:
    assert booking_text_match(text) is not None


@pytest.mark.parametrize("text", ["contact us", "about our team", "read our booklet", "quotes"])
def test_other_link_text_is_not_treated_as_booking(text: str) -> None:
    assert booking_text_match(text) is None


@pytest.mark.parametrize(
    ("generator", "expected"),
    [
        ("WordPress 6.5.2", "WordPress"),
        ("Wix.com Website Builder", "Wix"),
        ("Squarespace", "Squarespace"),
        ("Drupal 10 (https://www.drupal.org)", "Drupal"),
        ("Something Nobody Has Heard Of", None),
    ],
)
def test_the_generator_meta_maps_onto_a_platform(generator: str, expected: str | None) -> None:
    assert generator_label(generator) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LocalBusiness", True),
        ("Plumber", True),
        ("https://schema.org/Dentist", True),
        (["Organization", "HVACBusiness"], True),
        ("Organization", False),
        ("WebSite", False),
        (None, False),
        (42, False),
    ],
)
def test_local_business_types_are_recognised(raw: object, expected: bool) -> None:
    assert is_local_business_type(raw) is expected
