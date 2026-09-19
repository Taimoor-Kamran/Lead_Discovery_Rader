"""`GET /businesses`: the filters, the provenance on the detail view, and who may read it."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord
from app.modules.normalization.schemas import (
    Address,
    BusinessStatus,
    NormalizedBusiness,
    WebsiteKind,
)
from app.modules.resolution.survivorship import recompute, write_field_values
from app.modules.sources.models import Source, SourceKind
from tests.conftest import auth_headers, make_user

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def places(db: Session) -> Source:
    source = Source(name="google_places", kind=SourceKind.api, config={}, enabled=True)
    db.add(source)
    db.flush()
    return source


def build(
    session: Session,
    source: Source,
    place_id: str,
    **overrides: object,
) -> Business:
    """One business built the way resolution builds one: values first, then recompute."""
    fields: dict[str, object] = {
        "source": "google_places",
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
        "website": "https://abc.invalid/",
        "domain": "abc.invalid",
        "website_kind": WebsiteKind.own_site,
        "business_status": BusinessStatus.operational,
    }
    fields.update(overrides)
    normalized = NormalizedBusiness(**fields)  # type: ignore[arg-type]

    record = DiscoveredRecord(
        source_id=source.id,
        source_record_id=place_id,
        source_url=f"https://example.invalid/{place_id}",
        raw_payload={"id": place_id},
        first_discovered_at=NOW,
        last_discovered_at=NOW,
        content_expires_at=NOW + timedelta(days=30),
    )
    session.add(record)
    session.flush()

    business = Business(display_name=normalized.display_name)
    session.add(business)
    session.flush()
    record.business_id = business.id
    write_field_values(
        session, business=business, record=record, normalized=normalized, observed_at=NOW
    )
    recompute(session, business)
    session.flush()
    return business


@pytest.fixture
def populated(db: Session, places: Source) -> Iterator[list[Business]]:
    rows = [
        build(db, places, "p1"),
        build(
            db,
            places,
            "p2",
            display_name="Delgado Roofing",
            normalized_name="delgado roofing",
            industry="roofing",
            address=Address(city="Dallas", state="TX", postal_code="75201"),
            phone_e164="+12145550199",
            website=None,
            domain=None,
            website_kind=WebsiteKind.none,
        ),
        build(
            db,
            places,
            "p3",
            display_name="Closed Plumbers",
            normalized_name="closed plumbers",
            address=Address(city="Austin", state="TX", postal_code="78702"),
            phone_e164="+15125550188",
            website="https://x.wixsite.com/closed",
            domain="x.wixsite.com",
            website_kind=WebsiteKind.builder_subdomain,
            business_status=BusinessStatus.closed_permanently,
        ),
    ]
    db.commit()
    yield rows


def get(client: TestClient, user: User, path: str) -> dict[str, Any]:
    response = client.get(path, headers=auth_headers(client, user))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def names(body: dict[str, Any]) -> set[str]:
    return {item["display_name"] for item in body["items"]}


# --- listing and filters ------------------------------------------------------------


def test_every_authenticated_role_may_list_businesses(
    client: TestClient, db: Session, populated: list[Business]
) -> None:
    for role in Role:
        user = make_user(db, role)
        assert len(get(client, user, "/api/v1/businesses")["items"]) == 3


def test_listing_requires_authentication(client: TestClient, populated: list[Business]) -> None:
    assert client.get("/api/v1/businesses").status_code == 401


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("industry=roofing", {"Delgado Roofing"}),
        ("city=Austin", {"ABC Plumbing", "Closed Plumbers"}),
        ("city=austin", {"ABC Plumbing", "Closed Plumbers"}),
        ("state=tx", {"ABC Plumbing", "Delgado Roofing", "Closed Plumbers"}),
        ("has_website=true", {"ABC Plumbing", "Closed Plumbers"}),
        ("has_website=false", {"Delgado Roofing"}),
        ("website_kind=builder_subdomain", {"Closed Plumbers"}),
        ("business_status=closed_permanently", {"Closed Plumbers"}),
        ("q=plumb", {"ABC Plumbing", "Closed Plumbers"}),
        ("q=DELGADO", {"Delgado Roofing"}),
        ("city=Austin&industry=plumbing", {"ABC Plumbing", "Closed Plumbers"}),
        ("city=Austin&industry=roofing", set()),
    ],
)
def test_the_filters_narrow_the_list(
    client: TestClient,
    db: Session,
    sales_user: User,
    populated: list[Business],
    query: str,
    expected: set[str],
) -> None:
    assert names(get(client, sales_user, f"/api/v1/businesses?{query}")) == expected


def test_the_list_is_paginated(
    client: TestClient, sales_user: User, populated: list[Business]
) -> None:
    first = get(client, sales_user, "/api/v1/businesses?limit=2")

    assert len(first["items"]) == 2
    assert first["next_cursor"]

    second = get(client, sales_user, f"/api/v1/businesses?limit=2&cursor={first['next_cursor']}")
    assert len(second["items"]) == 1
    assert names(first) & names(second) == set()


# --- detail and provenance ----------------------------------------------------------


def test_the_detail_view_lists_the_source_of_every_field(
    client: TestClient, sales_user: User, populated: list[Business]
) -> None:
    """The acceptance criterion: every field says where it came from and when."""
    body = get(client, sales_user, f"/api/v1/businesses/{populated[0].id}")
    values = body["field_values"]

    assert values
    for value in values:
        assert value["source"] == "google_places"
        assert value["source_id"]
        assert value["discovered_record_id"]
        assert value["observed_at"]
        assert value["expires_at"], "Places-derived values carry the record's expiry"

    shown = {v["field"]: v for v in values}
    assert shown["phone_e164"]["value"] == "+15125550142"
    assert shown["phone_e164"]["is_displayed"] is True
    assert shown["display_name"]["value"] == "ABC Plumbing"


def test_the_detail_view_lists_the_records_behind_the_business(
    client: TestClient, sales_user: User, populated: list[Business]
) -> None:
    body = get(client, sales_user, f"/api/v1/businesses/{populated[0].id}")

    assert [r["source_record_id"] for r in body["records"]] == ["p1"]
    assert body["records"][0]["source"] == "google_places"


def test_an_unknown_business_is_404(client: TestClient, sales_user: User, db: Session) -> None:
    response = client.get(
        f"/api/v1/businesses/{uuid.uuid4()}", headers=auth_headers(client, sales_user)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
