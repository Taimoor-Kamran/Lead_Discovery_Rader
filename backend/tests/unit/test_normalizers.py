"""Every row of the normalization rules table (spec v0.3.0), example by example.

The rule these tests really enforce is the blueprint's: unknown stays unknown. A value
that cannot be parsed with confidence has to come out `None`, not a plausible guess.
"""

import pytest

from app.modules.adapters.base import AddressPart
from app.modules.normalization import addresses, names, phones, taxonomy
from app.modules.normalization.geo import distance_m, geohash7, neighbours
from app.modules.normalization.schemas import Address, BusinessStatus, WebsiteKind
from app.modules.normalization.web import (
    BUILDER_DOMAINS,
    SOCIAL_DOMAINS,
    builder_host,
    host_of,
    parse_website,
)

# --- names ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ABC Plumbing, LLC.", "ABC Plumbing, LLC."),
        ("  ABC   Plumbing  ", "ABC Plumbing"),
        ("ABC\tPlumbing\n", "ABC Plumbing"),
        ("ABC\u00a0Plumbing", "ABC Plumbing"),
        ("   ", None),
        (None, None),
    ],
)
def test_display_name_only_trims_and_collapses(raw: str | None, expected: str | None) -> None:
    assert names.display_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ABC Plumbing, LLC.", "abc plumbing"),
        ("ABC Plumbing & HVAC", "abc plumbing and hvac"),
        ("Smith & Sons Plumbing Inc", "smith and sons plumbing"),
        ("Acme Corporation", "acme"),
        ("Acme Co., Ltd.", "acme"),
        ("Rodriguez Plumbing PLLC", "rodriguez plumbing"),
        ("Café Olé Bakery", "cafe ole bakery"),
        ("LLC", "llc"),
        (None, None),
    ],
)
def test_normalized_name_follows_the_rules_table(raw: str | None, expected: str | None) -> None:
    assert names.normalize_name(raw) == expected


def test_a_legal_suffix_inside_the_name_is_kept() -> None:
    """Only trailing suffixes are stripped; `Cointreau` is not `Cointreau Co`."""
    assert names.normalize_name("Cointreau Imports") == "cointreau imports"
    assert names.normalize_name("Company Cleaners") == "company cleaners"


def test_name_key_is_phonetic_and_shared_by_spellings() -> None:
    assert names.name_key(names.normalize_name("Smith Plumbing")) == names.name_key(
        names.normalize_name("Smyth Plumbing")
    )
    assert names.name_key(names.normalize_name("Smith Plumbing")) != names.name_key(
        names.normalize_name("Delgado Roofing")
    )
    assert names.name_key(None) is None


# --- phones -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["(512) 555-0142", "512.555.0142", "+1 512 555 0142", "512-555-0142", " 5125550142 "],
)
def test_every_messy_spelling_of_one_number_gives_the_same_e164(raw: str) -> None:
    assert phones.to_e164(raw) == "+15125550142"


@pytest.mark.parametrize("raw", ["", "   ", None, "123", "555-0142", "not a phone", "+1 555"])
def test_an_invalid_phone_becomes_null_and_is_never_guessed(raw: str | None) -> None:
    assert phones.to_e164(raw) is None


def test_the_region_comes_from_the_address_country() -> None:
    assert phones.to_e164("020 7946 0958", region="GB") == "+442079460958"
    assert phones.to_e164("020 7946 0958", region="US") is None


# --- addresses --------------------------------------------------------------------


def component(long: str, short: str, *types: str) -> AddressPart:
    return AddressPart(long_text=long, short_text=short, types=types)


AUSTIN_COMPONENTS = [
    component("123", "123", "street_number"),
    component("Main Street", "Main St", "route"),
    component("4", "4", "subpremise"),
    component("Austin", "Austin", "locality"),
    component("Texas", "TX", "administrative_area_level_1"),
    component("78701-1234", "78701-1234", "postal_code"),
    component("United States", "US", "country"),
]


def test_structured_components_are_preferred_over_the_formatted_string() -> None:
    address = addresses.from_components(
        AUSTIN_COMPONENTS, formatted="somewhere else entirely, Dallas, TX 75201"
    )

    assert address.line1 == "123 Main Street"
    assert address.line2 == "Suite 4"
    assert address.street_key == "123 main street"
    assert address.city == "Austin"
    assert address.state == "TX"
    assert address.postal_code == "78701"
    assert address.country == "US"


def test_without_components_only_city_state_and_postal_are_read_back() -> None:
    address = addresses.from_components(None, formatted="123 Main St. Ste 4, Austin, TX 78701, USA")

    assert address.city == "Austin"
    assert address.state == "TX"
    assert address.postal_code == "78701"
    assert address.line1 is None, "a formatted address is display text, not a parsed street"
    assert address.street_key is None


def test_an_unparseable_formatted_address_leaves_everything_null() -> None:
    address = addresses.from_components(None, formatted="Behind the old mill, ask for Dave")

    assert address == Address()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Texas", "TX"), ("texas", "TX"), ("TX", "TX"), ("tx", "TX"), ("Narnia", None), (None, None)],
)
def test_state_names_map_to_usps_codes(raw: str | None, expected: str | None) -> None:
    assert addresses.state_code(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("78701-1234", "78701"), ("78701", "78701"), ("787", None), (None, None)],
)
def test_us_postal_codes_drop_the_plus_four(raw: str | None, expected: str | None) -> None:
    assert addresses.postal_code(raw) == expected


def test_a_non_us_postal_code_is_kept_as_given() -> None:
    assert addresses.postal_code("SW1A 1AA", country="GB") == "SW1A 1AA"


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("123", "Main St."), "123 main street"),
        (("123", "Main Street"), "123 main street"),
        (("500", "N Lamar Blvd"), "500 north lamar boulevard"),
        (("9", "Oak Ave"), "9 oak avenue"),
        (("1", "SW Cedar Rd"), "1 southwest cedar road"),
        ((None, None), None),
    ],
)
def test_street_key_expands_abbreviations(parts: tuple[str | None, ...], expected: str) -> None:
    assert addresses.street_key(*parts) == expected


# --- websites ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "domain"),
    [
        ("https://www.ABC.com/", "abc.com"),
        ("WWW.ABC.COM/", "abc.com"),
        ("http://abc.com:8080/services", "abc.com"),
        ("https://abc.com/a?utm=1#x", "abc.com"),
    ],
)
def test_a_normal_site_keeps_its_registered_domain(raw: str, domain: str) -> None:
    _, parsed_domain, kind = parse_website(raw)

    assert parsed_domain == domain
    assert kind is WebsiteKind.own_site


def test_the_stored_website_keeps_the_url_as_given_minus_query_and_fragment() -> None:
    website, _, _ = parse_website("https://www.ABC.com/services?utm_source=x#top")

    assert website == "https://www.ABC.com/services"


@pytest.mark.parametrize("builder", sorted(BUILDER_DOMAINS))
def test_a_builder_subdomain_is_identified_by_its_whole_host(builder: str) -> None:
    website, domain, kind = parse_website(f"https://joesplumbing.{builder}/home")

    assert kind is WebsiteKind.builder_subdomain
    assert domain == f"joesplumbing.{builder}", "two builder sites must never share a domain"
    assert website is not None


@pytest.mark.parametrize("social", sorted(SOCIAL_DOMAINS))
def test_a_social_profile_has_no_domain(social: str) -> None:
    website, domain, kind = parse_website(f"https://{social}/abcplumbing")

    assert kind is WebsiteKind.social_profile
    assert domain is None, "two businesses must never match on being on the same platform"
    assert website is not None


@pytest.mark.parametrize("raw", [None, "", "   ", "not a url", "tel:5125550142"])
def test_no_website_means_no_domain_and_kind_none(raw: str | None) -> None:
    assert parse_website(raw) == (None, None, WebsiteKind.none)


def test_an_unknown_suffix_falls_back_to_the_whole_host() -> None:
    """A reserved or brand-new TLD keeps its host as the identity rather than losing one."""
    website, domain, kind = parse_website("https://www.lonestarplumbing.invalid/services")

    assert domain == "lonestarplumbing.invalid"
    assert kind is WebsiteKind.own_site
    assert website == "https://www.lonestarplumbing.invalid/services"


def test_two_unknown_suffix_hosts_stay_different_businesses() -> None:
    _, left, _ = parse_website("https://abc.invalid/")
    _, right, _ = parse_website("https://xyz.invalid/")

    assert left != right


def test_host_of_drops_www_and_the_port() -> None:
    assert host_of("https://WWW.Example.com:8443/x") == "example.com"
    assert host_of("localhost") is None


# --- taxonomy and status -----------------------------------------------------------


@pytest.mark.parametrize(
    ("primary", "expected"),
    [
        ("plumber", "plumbing"),
        ("electrician", "electrical"),
        ("roofing_contractor", "roofing"),
        ("general_contractor", "general_contracting"),
        ("dentist", "dental"),
        ("lawyer", "legal"),
        ("restaurant", "restaurant"),
        ("interpretive_dance_studio", "other"),
        (None, "other"),
    ],
)
def test_places_types_map_to_industry_slugs(primary: str | None, expected: str) -> None:
    assert taxonomy.to_industry(primary) == expected


def test_a_secondary_type_is_used_when_the_primary_one_is_unknown() -> None:
    assert taxonomy.to_industry("point_of_interest", ["point_of_interest", "plumber"]) == "plumbing"


def test_an_unmapped_type_keeps_its_raw_types_and_becomes_other() -> None:
    assert taxonomy.to_industry("yak_shaving", ["yak_shaving"]) == taxonomy.OTHER


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OPERATIONAL", BusinessStatus.operational),
        ("CLOSED_TEMPORARILY", BusinessStatus.closed_temporarily),
        ("CLOSED_PERMANENTLY", BusinessStatus.closed_permanently),
        ("SOMETHING_NEW", BusinessStatus.unknown),
        (None, BusinessStatus.unknown),
    ],
)
def test_business_status_maps_or_stays_unknown(raw: str | None, expected: BusinessStatus) -> None:
    assert taxonomy.to_business_status(raw) == expected


# --- geo --------------------------------------------------------------------------


def test_geohash7_is_stable_and_missing_coordinates_give_null() -> None:
    assert geohash7(30.2672, -97.7431) == "9v6kpvc"
    assert geohash7(None, -97.7431) is None
    assert geohash7(30.2672, None) is None
    assert geohash7(120.0, 0.0) is None


def test_the_neighbourhood_is_the_cell_plus_eight() -> None:
    cells = neighbours(30.2672, -97.7431)

    assert len(cells) == 9
    assert geohash7(30.2672, -97.7431) in cells


def test_distance_is_none_when_either_point_is_unknown() -> None:
    assert distance_m(30.0, -97.0, None, None) is None
    assert distance_m(None, None, 30.0, -97.0) is None


def test_distance_between_two_known_points_is_metres() -> None:
    metres = distance_m(30.2672, -97.7431, 30.2672, -97.7421)

    assert 90 < (metres or 0) < 100


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://topelectricianaustin.wixstudio.com/", "topelectricianaustin.wixstudio.com"),
        ("https://www.joes.wixsite.com/home", "joes.wixsite.com"),
        ("https://wixsite.com/", None),
        ("https://joesplumbing.com/", None),
        ("https://notwixsite.com/", None),
        (None, None),
    ],
)
def test_builder_host_is_the_host_only_when_it_is_a_builder_subdomain(
    url: str | None, expected: str | None
) -> None:
    assert builder_host(url) == expected
