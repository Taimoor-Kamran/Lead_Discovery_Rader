"""Survivorship: which source's answer a business shows, and what happens when it expires.

Nothing on a `businesses` row is a fact of its own — every value is the winner among the
field values written beneath it. That is what lets a purge take a value away without
losing the business, and what makes a merge visible immediately.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.businesses.models import PROVENANCED_FIELDS, Business, BusinessFieldValue
from app.modules.discovery.models import DiscoveredRecord
from app.modules.normalization.schemas import (
    Address,
    BusinessStatus,
    NormalizedBusiness,
    WebsiteKind,
)
from app.modules.resolution.survivorship import (
    EXPIRED_NAME_TEMPLATE,
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
