"""Blocking: which businesses a record is compared against, and which it is not.

Blocking is a recall/cost trade-off. These tests pin down what it is guaranteed to find
(a shared domain, phone, postal+name or map cell) and what it must never find on its own
(a shared social platform, an unrelated name in the same street).
"""

from sqlalchemy.orm import Session

from app.modules.businesses.models import Business
from app.modules.normalization.schemas import (
    Address,
    BusinessStatus,
    NormalizedBusiness,
    WebsiteKind,
)
from app.modules.resolution.blocking import candidate_keys, find_candidates

AUSTIN = (30.2672, -97.7431)


def record(**overrides: object) -> NormalizedBusiness:
    fields: dict[str, object] = {
        "source": "google_places",
        "display_name": "ABC Plumbing",
        "normalized_name": "abc plumbing",
        "name_key": "ABKPLMBNK",
        "address": Address(
            street_key="123 main street", city="Austin", state="TX", postal_code="78701"
        ),
        "lat": AUSTIN[0],
        "lng": AUSTIN[1],
        "geohash7": "9v6kpvc",
        "phone_e164": "+15125550142",
        "domain": "abc.example.com",
        "website_kind": WebsiteKind.own_site,
        "business_status": BusinessStatus.operational,
    }
    fields.update(overrides)
    return NormalizedBusiness(**fields)  # type: ignore[arg-type]


def add_business(session: Session, **fields: object) -> Business:
    business = Business(display_name=str(fields.pop("display_name", "Some Business")), **fields)
    session.add(business)
    session.flush()
    return business


# --- keys ---------------------------------------------------------------------------


def test_a_social_profile_is_never_a_blocking_key() -> None:
    keys = candidate_keys(record(domain=None, website_kind=WebsiteKind.social_profile))

    assert keys.domain is None


def test_a_record_with_nothing_to_block_on_has_no_keys() -> None:
    keys = candidate_keys(
        record(
            domain=None,
            phone_e164=None,
            name_key=None,
            address=Address(),
            lat=None,
            lng=None,
            geohash7=None,
        )
    )

    assert keys.is_empty is True


def test_the_geohash_key_is_the_cell_and_its_neighbours() -> None:
    assert len(candidate_keys(record()).geohash_cells) == 9


# --- lookups ------------------------------------------------------------------------


def test_nothing_to_block_on_finds_nothing(db: Session) -> None:
    add_business(db, display_name="ABC Plumbing", normalized_name="abc plumbing")
    db.commit()

    empty = record(
        domain=None, phone_e164=None, name_key=None, address=Address(), lat=None, lng=None
    )

    assert find_candidates(db, empty) == []


def test_a_shared_domain_is_found(db: Session) -> None:
    match = add_business(db, display_name="ABC", domain="abc.example.com")
    add_business(db, display_name="Other", domain="other.example.com")
    db.commit()

    assert [c.id for c in find_candidates(db, record())] == [match.id]


def test_a_shared_phone_is_found(db: Session) -> None:
    match = add_business(db, display_name="ABC", phone_e164="+15125550142")
    db.commit()

    assert [c.id for c in find_candidates(db, record(domain=None))] == [match.id]


def test_a_shared_postal_code_and_name_key_is_found(db: Session) -> None:
    match = add_business(db, display_name="ABC", postal_code="78701", name_key="ABKPLMBNK")
    add_business(db, display_name="Elsewhere", postal_code="73301", name_key="ABKPLMBNK")
    db.commit()

    found = find_candidates(db, record(domain=None, phone_e164=None, lat=None, lng=None))

    assert [c.id for c in found] == [match.id]


def test_a_neighbouring_map_cell_with_a_similar_name_is_found(db: Session) -> None:
    """About 120 m away — a different geohash cell, but a neighbouring one."""
    near = add_business(
        db,
        display_name="ABC Plumbing and Heating",
        normalized_name="abc plumbing and heating",
        lat=AUSTIN[0] + 0.0011,
        lng=AUSTIN[1],
        geohash7="9v6kpvf",
    )
    db.commit()
    from app.modules.normalization.geo import geohash7

    near.geohash7 = geohash7(near.lat, near.lng)
    db.commit()

    found = find_candidates(
        db, record(domain=None, phone_e164=None, address=Address(city="Austin"))
    )

    assert [c.id for c in found] == [near.id]


def test_a_neighbour_with_an_unrelated_name_is_not_a_candidate(db: Session) -> None:
    from app.modules.normalization.geo import geohash7

    add_business(
        db,
        display_name="Delgado Roofing",
        normalized_name="delgado roofing",
        lat=AUSTIN[0],
        lng=AUSTIN[1],
        geohash7=geohash7(*AUSTIN),
    )
    db.commit()

    found = find_candidates(
        db, record(domain=None, phone_e164=None, address=Address(city="Austin"))
    )

    assert found == []


def test_the_candidate_list_is_capped(db: Session) -> None:
    for index in range(30):
        add_business(db, display_name=f"ABC {index}", domain="abc.example.com")
    db.commit()

    assert len(find_candidates(db, record(), limit=25)) == 25


def test_a_strong_key_beats_a_map_cell_when_the_cap_bites(db: Session) -> None:
    """When more candidates exist than fit, the shared-domain ones are the ones kept."""
    from app.modules.normalization.geo import geohash7

    by_domain = add_business(db, display_name="ABC", domain="abc.example.com")
    for index in range(5):
        add_business(
            db,
            display_name=f"ABC Plumbing {index}",
            normalized_name="abc plumbing",
            lat=AUSTIN[0],
            lng=AUSTIN[1],
            geohash7=geohash7(*AUSTIN),
        )
    db.commit()

    assert [c.id for c in find_candidates(db, record(), limit=1)] == [by_domain.id]


def test_an_excluded_business_is_never_returned(db: Session) -> None:
    match = add_business(db, display_name="ABC", domain="abc.example.com")
    db.commit()

    assert find_candidates(db, record(), exclude={match.id}) == []
