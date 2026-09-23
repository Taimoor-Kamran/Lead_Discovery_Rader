"""`normalize()` end to end: one candidate in, one `NormalizedBusiness` out.

Two properties matter more than the mapping itself — an absent field stays `None`, and a
record that cannot be described as a business raises rather than being quietly dropped.
"""

import httpx
import pytest
import respx

from app.modules.adapters.base import AddressPart, Candidate
from app.modules.normalization.normalize import NormalizationError, normalize
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind

SOURCE = "google_places"


def austin_candidate(**overrides: object) -> Candidate:
    base: dict[str, object] = {
        "display_name": "ABC Plumbing, LLC.",
        "formatted_address": "123 Main St. Ste 4, Austin, TX 78701-1234, USA",
        "phone": "(512) 555-0142",
        "website": "https://www.ABC.com/",
        "business_status": "OPERATIONAL",
        "types": ["plumber", "point_of_interest"],
        "primary_type": "plumber",
        "lat": 30.2672,
        "lng": -97.7431,
        "address_components": [
            AddressPart("123", "123", ("street_number",)),
            AddressPart("Main Street", "Main St", ("route",)),
            AddressPart("4", "4", ("subpremise",)),
            AddressPart("Austin", "Austin", ("locality",)),
            AddressPart("Texas", "TX", ("administrative_area_level_1",)),
            AddressPart("78701-1234", "78701-1234", ("postal_code",)),
            AddressPart("United States", "US", ("country",)),
        ],
    }
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def test_a_complete_candidate_maps_to_every_field() -> None:
    result = normalize(austin_candidate(), SOURCE)

    assert result.source == SOURCE
    assert result.display_name == "ABC Plumbing, LLC."
    assert result.normalized_name == "abc plumbing"
    assert result.name_key
    assert result.industry == "plumbing"
    assert result.raw_types == ["plumber", "point_of_interest"]
    assert result.address.street_key == "123 main street"
    assert result.address.city == "Austin"
    assert result.address.state == "TX"
    assert result.address.postal_code == "78701"
    assert result.phone_e164 == "+15125550142"
    assert result.domain == "abc.com"
    assert result.website_kind is WebsiteKind.own_site
    assert result.business_status is BusinessStatus.operational
    assert result.geohash7 == "9v6kpvc"


def test_an_empty_candidate_apart_from_a_name_leaves_everything_else_null() -> None:
    result = normalize(Candidate(display_name="Nameless Co"), SOURCE)

    assert result.display_name == "Nameless Co"
    assert result.phone_e164 is None
    assert result.website is None
    assert result.domain is None
    assert result.website_kind is WebsiteKind.none
    assert result.business_status is BusinessStatus.unknown
    assert result.industry == "other"
    assert result.geohash7 is None
    assert result.address.city is None


def test_an_invalid_phone_becomes_null_rather_than_a_guess() -> None:
    assert normalize(austin_candidate(phone="555-0142"), SOURCE).phone_e164 is None


def test_a_social_profile_website_gives_a_null_domain() -> None:
    result = normalize(austin_candidate(website="https://facebook.com/abcplumbing"), SOURCE)

    assert result.website_kind is WebsiteKind.social_profile
    assert result.domain is None
    assert result.website == "https://facebook.com/abcplumbing"


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(30.2672, None), (None, -97.7431), (999.0, -97.7431), (30.2672, 999.0)],
)
def test_a_half_known_or_impossible_coordinate_is_no_coordinate(
    lat: float | None, lng: float | None
) -> None:
    result = normalize(austin_candidate(lat=lat, lng=lng), SOURCE)

    assert (result.lat, result.lng, result.geohash7) == (None, None, None)


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_record_with_no_name_is_invalid_and_says_why(name: str | None) -> None:
    with pytest.raises(NormalizationError) as caught:
        normalize(austin_candidate(display_name=name), SOURCE)

    assert any("display_name" in error for error in caught.value.errors)


def test_normalizing_is_deterministic() -> None:
    assert normalize(austin_candidate(), SOURCE) == normalize(austin_candidate(), SOURCE)


def test_tldextract_never_reaches_the_network(mock_http: respx.MockRouter) -> None:
    """The suffix list is the bundled snapshot. A download would fail this test.

    `mock_http` fails any unmocked outbound call; this route exists only so that a
    request to the public suffix list would be recorded rather than merely refused.
    """
    suffix_list = mock_http.get(url__regex=r".*public_suffix_list.*").mock(
        return_value=httpx.Response(200, text="com\n")
    )

    for url in ["https://www.abc.com/", "https://x.wixsite.com/y", "https://shop.abc.co.uk/"]:
        normalize(austin_candidate(website=url), SOURCE)

    assert not suffix_list.called
    assert not mock_http.calls


# --- v0.11.0: rating and review count ------------------------------------------------------


@pytest.mark.parametrize(
    ("rating", "count", "expected"),
    [
        (4.6, 11, (4.6, 11)),
        (1.0, 0, (1.0, 0)),
        (None, None, (None, None)),
        (7.5, -3, (None, None)),  # not a rating or a count: null, never clamped
        (0.0, 5, (None, 5)),
    ],
)
def test_rating_and_review_count_are_kept_only_when_they_are_plausible(
    rating: float | None, count: int | None, expected: tuple[float | None, int | None]
) -> None:
    normalized = normalize(
        austin_candidate(rating=rating, user_rating_count=count), "google_places"
    )

    assert (normalized.rating, normalized.user_rating_count) == expected
