"""Survivorship: which source's answer a business shows, and what happens when it expires.

Nothing on a `businesses` row is a fact of its own — every value is the winner among the
field values written beneath it. That is what lets a purge take a value away without
losing the business, and what makes a merge visible immediately.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.adapters.base import RawDoc
from app.modules.adapters.demo_fixture import SOURCE_NAME as DEMO_SOURCE
from app.modules.adapters.demo_fixture import DemoFixtureAdapter, demo_places
from app.modules.businesses.models import PROVENANCED_FIELDS, Business, BusinessFieldValue
from app.modules.discovery.models import DiscoveredRecord
from app.modules.normalization.normalize import normalize
from app.modules.normalization.schemas import (
    Address,
    BusinessStatus,
    NormalizedBusiness,
    WebsiteKind,
)
from app.modules.resolution.survivorship import (
    ADDRESS_GROUP,
    EXPIRED_NAME_TEMPLATE,
    WEBSITE_GROUP,
    field_values,
    recompute,
    write_field_values,
)
from app.modules.sources.models import Source, SourceKind

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def source(session: Session, name: str) -> Source:
    row = Source(name=name, kind=SourceKind.api, config={}, enabled=True)
    session.add(row)
    session.flush()
    return row


def record(
    session: Session, source_row: Source, place_id: str, **overrides: object
) -> DiscoveredRecord:
    fields: dict[str, object] = {
        "source_id": source_row.id,
        "source_record_id": place_id,
        "raw_payload": {"id": place_id},
        "first_discovered_at": NOW,
        "last_discovered_at": NOW,
        "content_expires_at": NOW + timedelta(days=30),
    }
    fields.update(overrides)
    row = DiscoveredRecord(**fields)
    session.add(row)
    session.flush()
    return row


def normalized(source_name: str, **overrides: object) -> NormalizedBusiness:
    fields: dict[str, object] = {
        "source": source_name,
        "display_name": "ABC Plumbing",
        "normalized_name": "abc plumbing",
        "name_key": "ABKPLMBNK",
        "industry": "plumbing",
        "address": Address(
            line1="123 Main Street",
            street_key="123 main street",
            city="Austin",
            state="TX",
            postal_code="78701",
            country="US",
        ),
        "lat": 30.2672,
        "lng": -97.7431,
        "geohash7": "9v6kpvc",
        "phone_e164": "+15125550142",
        "website": "https://abc.example.com/",
        "domain": "abc.example.com",
        "website_kind": WebsiteKind.own_site,
        "business_status": BusinessStatus.operational,
    }
    fields.update(overrides)
    return NormalizedBusiness(**fields)  # type: ignore[arg-type]


def new_business(session: Session) -> Business:
    business = Business(display_name="placeholder")
    session.add(business)
    session.flush()
    return business


def demo_normalized(place_id: str) -> NormalizedBusiness:
    """One demo place put through the very normalizer the loader uses."""
    payload = next(place for place in demo_places() if place["id"] == place_id)
    raw = RawDoc(
        source=DEMO_SOURCE,
        source_record_id=place_id,
        source_url=None,
        payload=payload,
        fetched_at=NOW,
    )
    return normalize(DemoFixtureAdapter().normalize(raw), DEMO_SOURCE)


# --- writing ------------------------------------------------------------------------


def test_every_provenanced_field_gets_a_row(db: Session) -> None:
    places = source(db, "google_places")
    row = record(db, places, "ChIJone")
    business = new_business(db)

    write_field_values(
        db, business=business, record=row, normalized=normalized("google_places"), observed_at=NOW
    )
    db.commit()

    fields = {value.field for value in db.scalars(select(BusinessFieldValue))}
    assert fields == set(PROVENANCED_FIELDS)


def test_a_null_value_is_stored_as_null_and_not_omitted(db: Session) -> None:
    places = source(db, "google_places")
    row = record(db, places, "ChIJone")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=row,
        normalized=normalized("google_places", phone_e164=None),
        observed_at=NOW,
    )
    db.commit()

    phone = db.scalars(
        select(BusinessFieldValue).where(BusinessFieldValue.field == "phone_e164")
    ).one()
    assert phone.value is None


def test_rewriting_the_same_record_updates_rather_than_duplicates(db: Session) -> None:
    places = source(db, "google_places")
    row = record(db, places, "ChIJone")
    business = new_business(db)

    write_field_values(
        db, business=business, record=row, normalized=normalized("google_places"), observed_at=NOW
    )
    write_field_values(
        db,
        business=business,
        record=row,
        normalized=normalized("google_places", phone_e164="+15125550199"),
        observed_at=NOW + timedelta(days=1),
    )
    db.commit()

    rows = list(
        db.scalars(select(BusinessFieldValue).where(BusinessFieldValue.field == "phone_e164"))
    )
    assert len(rows) == 1
    assert rows[0].value == "+15125550199"


def test_the_expiry_of_a_value_follows_the_record_it_came_from(db: Session) -> None:
    places = source(db, "google_places")
    row = record(db, places, "ChIJone")
    business = new_business(db)

    written = write_field_values(
        db, business=business, record=row, normalized=normalized("google_places"), observed_at=NOW
    )
    db.commit()

    assert all(value.expires_at == row.content_expires_at for value in written)


def test_the_flattened_values_cover_every_field() -> None:
    assert set(field_values(normalized("google_places"))) == set(PROVENANCED_FIELDS)


# --- recomputing --------------------------------------------------------------------


def test_recompute_fills_the_business_from_its_field_values(db: Session) -> None:
    places = source(db, "google_places")
    row = record(db, places, "ChIJone")
    business = new_business(db)
    write_field_values(
        db, business=business, record=row, normalized=normalized("google_places"), observed_at=NOW
    )

    recompute(db, business)
    db.commit()

    assert business.display_name == "ABC Plumbing"
    assert business.normalized_name == "abc plumbing"
    assert business.city == "Austin"
    assert business.postal_code == "78701"
    assert business.phone_e164 == "+15125550142"
    assert business.domain == "abc.example.com"
    assert business.lat == 30.2672
    assert business.website_kind is WebsiteKind.own_site
    assert business.business_status is BusinessStatus.operational


def test_the_higher_priority_source_wins(db: Session) -> None:
    """`RESOLUTION_SOURCE_PRIORITY` puts google_places ahead of demo_fixture."""
    places = source(db, "google_places")
    demo = source(db, "demo_fixture")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, demo, "demo-1"),
        normalized=normalized("demo_fixture", phone_e164="+15125550111"),
        observed_at=NOW + timedelta(days=5),
    )
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJone"),
        normalized=normalized("google_places", phone_e164="+15125550142"),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.phone_e164 == "+15125550142", "priority beats recency"


def test_within_one_source_the_most_recent_value_wins(db: Session) -> None:
    places = source(db, "google_places")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJold"),
        normalized=normalized("google_places", phone_e164="+15125550111"),
        observed_at=NOW - timedelta(days=5),
    )
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJnew"),
        normalized=normalized("google_places", phone_e164="+15125550142"),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.phone_e164 == "+15125550142"


def test_a_null_from_the_preferred_source_falls_through_to_the_next(db: Session) -> None:
    places = source(db, "google_places")
    demo = source(db, "demo_fixture")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, demo, "demo-1"),
        normalized=normalized("demo_fixture", website="https://abc.example.com/"),
        observed_at=NOW,
    )
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJone"),
        normalized=normalized("google_places", website=None, domain=None),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.website == "https://abc.example.com/", "a known value beats a null"


def test_a_purged_value_stops_counting(db: Session) -> None:
    places = source(db, "google_places")
    row = record(db, places, "ChIJone")
    business = new_business(db)
    write_field_values(
        db, business=business, record=row, normalized=normalized("google_places"), observed_at=NOW
    )
    recompute(db, business)
    db.commit()

    for value in db.scalars(select(BusinessFieldValue)):
        value.value = None
        value.purged_at = NOW
    db.commit()

    row.business_id = business.id
    db.commit()
    recompute(db, business)
    db.commit()

    assert business.phone_e164 is None
    assert business.city is None
    assert business.display_name == EXPIRED_NAME_TEMPLATE.format(source_record_id="ChIJone")
    assert business.business_status is BusinessStatus.unknown
    assert business.website_kind is WebsiteKind.none


def test_the_earliest_expiry_is_shown_on_the_business(db: Session) -> None:
    places = source(db, "google_places")
    early = record(db, places, "ChIJearly", content_expires_at=NOW + timedelta(days=3))
    late = record(db, places, "ChIJlate", content_expires_at=NOW + timedelta(days=30))
    business = new_business(db)

    write_field_values(
        db, business=business, record=late, normalized=normalized("google_places"), observed_at=NOW
    )
    write_field_values(
        db, business=business, record=early, normalized=normalized("google_places"), observed_at=NOW
    )

    recompute(db, business)
    db.commit()

    assert business.places_content_expires_at == NOW + timedelta(days=3)


# --- fields that survive together ---------------------------------------------------


def test_the_two_groups_are_disjoint_and_are_real_fields() -> None:
    assert not set(WEBSITE_GROUP) & set(ADDRESS_GROUP)
    assert set(WEBSITE_GROUP) | set(ADDRESS_GROUP) <= set(PROVENANCED_FIELDS)


def test_the_web_presence_comes_whole_from_one_record(db: Session) -> None:
    """A Facebook page from one record must not be shown with another record's domain."""
    places = source(db, "google_places")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJsite"),
        normalized=normalized(
            "google_places",
            website="https://abc.invalid/",
            domain="abc.invalid",
            website_kind=WebsiteKind.own_site,
        ),
        observed_at=NOW - timedelta(days=5),
    )
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJsocial"),
        normalized=normalized(
            "google_places",
            website="https://www.facebook.com/abcplumbing",
            domain=None,
            website_kind=WebsiteKind.social_profile,
        ),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.website == "https://abc.invalid/"
    assert business.domain == "abc.invalid"
    assert business.website_kind is WebsiteKind.own_site


def test_a_builder_subdomain_beats_a_social_page(db: Session) -> None:
    places = source(db, "google_places")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJbuilder"),
        normalized=normalized(
            "google_places",
            website="https://abcplumbing.wixsite.com/home",
            domain="abcplumbing.wixsite.com",
            website_kind=WebsiteKind.builder_subdomain,
        ),
        observed_at=NOW - timedelta(days=5),
    )
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJsocial"),
        normalized=normalized(
            "google_places",
            website="https://www.facebook.com/abcplumbing",
            domain=None,
            website_kind=WebsiteKind.social_profile,
        ),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.domain == "abcplumbing.wixsite.com"
    assert business.website_kind is WebsiteKind.builder_subdomain


def test_a_social_page_is_shown_with_no_domain_when_it_is_all_there_is(db: Session) -> None:
    """Winning the group must never invent a domain the source did not report."""
    places = source(db, "google_places")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJsocial"),
        normalized=normalized(
            "google_places",
            website="https://www.facebook.com/abcplumbing",
            domain=None,
            website_kind=WebsiteKind.social_profile,
        ),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.website == "https://www.facebook.com/abcplumbing"
    assert business.domain is None
    assert business.website_kind is WebsiteKind.social_profile


def test_the_oak_hill_pair_from_the_demo_data_keeps_one_web_presence(db: Session) -> None:
    """The case found in manual testing: merged, the pair mixed Facebook with a domain.

    `demo-b4-1` has its own site, `demo-b4-2` only a Facebook page. A reviewer who merges
    the two must see one record's answer, not a business whose website is a Facebook page
    while its domain comes from somewhere else entirely.
    """
    demo = source(db, DEMO_SOURCE)
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, demo, "demo-b4-1"),
        normalized=demo_normalized("demo-b4-1"),
        observed_at=NOW,
    )
    # Merged by a reviewer afterwards, so it is the more recent of the two.
    write_field_values(
        db,
        business=business,
        record=record(db, demo, "demo-b4-2"),
        normalized=demo_normalized("demo-b4-2"),
        observed_at=NOW + timedelta(minutes=5),
    )

    recompute(db, business)
    db.commit()

    assert business.website == "https://oakhillplumbing.invalid/"
    assert business.domain == "oakhillplumbing.invalid"
    assert business.website_kind is WebsiteKind.own_site
    # The address is one record's too - 910 Patton Ranch Road, not a blend of both.
    assert business.address_line1 == "910 Patton Ranch Road"
    assert business.street_key == "910 patton ranch road"
    assert business.lat == 30.4249


def test_the_address_comes_whole_from_one_record(db: Session) -> None:
    """A newer record's street must not be shown with an older record's suite or postcode."""
    places = source(db, "google_places")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJold"),
        normalized=normalized(
            "google_places",
            address=Address(
                line1="123 Main Street",
                line2="Suite 4",
                street_key="123 main street",
                city="Austin",
                state="TX",
                postal_code="78701",
                country="US",
            ),
            lat=30.2672,
            lng=-97.7431,
            geohash7="9v6kpvc",
        ),
        observed_at=NOW - timedelta(days=5),
    )
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJnew"),
        normalized=normalized(
            "google_places",
            address=Address(
                line1="900 Patton Ranch Road",
                street_key="900 patton ranch road",
                city="Austin",
                state="TX",
                postal_code="78745",
                country="US",
            ),
            lat=30.424,
            lng=-97.745,
            geohash7="9v6mpf8",
        ),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.address_line1 == "900 Patton Ranch Road"
    assert business.address_line2 is None, "the older record's suite must not leak through"
    assert business.street_key == "900 patton ranch road"
    assert business.postal_code == "78745"
    assert business.lat == 30.424
    assert business.geohash7 == "9v6mpf8"


def test_a_street_address_beats_a_more_recent_town_only_one(db: Session) -> None:
    places = source(db, "google_places")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJstreet"),
        normalized=normalized(
            "google_places",
            address=Address(
                line1="123 Main Street",
                street_key="123 main street",
                city="Austin",
                state="TX",
                postal_code="78701",
                country="US",
            ),
        ),
        observed_at=NOW - timedelta(days=5),
    )
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJtown"),
        normalized=normalized(
            "google_places",
            address=Address(city="Round Rock", state="TX", country="US"),
            lat=None,
            lng=None,
            geohash7=None,
        ),
        observed_at=NOW,
    )

    recompute(db, business)
    db.commit()

    assert business.address_line1 == "123 Main Street"
    assert business.city == "Austin", "the town of a vaguer record must not override its street"
    assert business.postal_code == "78701"


def test_a_record_with_no_address_at_all_never_wins_the_group(db: Session) -> None:
    places = source(db, "google_places")
    demo = source(db, "demo_fixture")
    business = new_business(db)

    write_field_values(
        db,
        business=business,
        record=record(db, demo, "demo-1"),
        normalized=normalized("demo_fixture"),
        observed_at=NOW,
    )
    # The preferred source knows the business but not where it is.
    write_field_values(
        db,
        business=business,
        record=record(db, places, "ChIJnowhere"),
        normalized=normalized(
            "google_places", address=Address(), lat=None, lng=None, geohash7=None
        ),
        observed_at=NOW + timedelta(days=1),
    )

    recompute(db, business)
    db.commit()

    assert business.city == "Austin"
    assert business.street_key == "123 main street"
