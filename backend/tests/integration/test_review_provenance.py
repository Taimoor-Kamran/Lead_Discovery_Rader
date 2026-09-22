"""Spec v0.10.0: the source block, the source column and the "found within" filter.

The question this answers on screen is the one a prospect asks: *where did you get my
details?* Nothing here changes what is fetched or stored — every value already exists in
`discovered_records` and in the latest website audit.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import EllipsisType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.audit_web.checks import CheckResult, as_payload
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from app.modules.sources.models import Source, SourceKind
from tests.conftest import auth_headers, make_user

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
PAGE = "https://bartoncreekplumbing.invalid/"


# --- helpers ----------------------------------------------------------------------------


@pytest.fixture
def places(db: Session) -> Source:
    row = Source(
        name="google_places",
        kind=SourceKind.api,
        config={"display_name": "Google Places API (New)"},
        enabled=True,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def demo(db: Session) -> Source:
    row = Source(
        name="demo_fixture",
        kind=SourceKind.api,
        config={"display_name": "Demo fixture (Austin plumbers)"},
        enabled=True,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer, "reviewer@example.com")


@pytest.fixture
def rep(db: Session) -> User:
    return make_user(db, Role.sales_rep, "rep1@example.com")


def make_business(
    session: Session,
    *,
    name: str = "Barton Creek Plumbing",
    city: str = "Austin",
    state: str = "TX",
    industry: str = "plumbing",
) -> Business:
    business = Business(
        display_name=name,
        industry=industry,
        website=PAGE,
        domain=f"{uuid.uuid4().hex[:6]}.invalid",
        website_kind=WebsiteKind.own_site,
        business_status=BusinessStatus.operational,
        city=city,
        state=state,
        phone_e164="+15125550100",
    )
    session.add(business)
    session.flush()
    return business


def make_record(
    session: Session,
    source: Source,
    business: Business,
    *,
    record_id: str | None = None,
    # `...` keeps the derived default; an explicit `None` means the source gave no URL.
    source_url: str | EllipsisType | None = ...,
    first_discovered_at: datetime = NOW,
    last_discovered_at: datetime | None = None,
) -> DiscoveredRecord:
    key = record_id or f"rec-{uuid.uuid4().hex[:8]}"
    record = DiscoveredRecord(
        source_id=source.id,
        source_record_id=key,
        source_url=f"https://maps.invalid/{key}" if source_url is ... else source_url,
        raw_payload={"id": key},
        business_id=business.id,
        first_discovered_at=first_discovered_at,
        last_discovered_at=last_discovered_at or first_discovered_at,
    )
    session.add(record)
    session.flush()
    return record


def make_audit(
    session: Session,
    business: Business,
    *,
    social: list[str] | None = None,
    evidence: str | None = None,
) -> WebsiteAudit:
    checks: dict[str, CheckResult] = {"parsed": CheckResult(True, evidence_url=PAGE)}
    if social is not None:
        checks["social_links"] = CheckResult(
            social,
            evidence_text=evidence
            or "; ".join(f"{key}: https://www.{key}.invalid/barton" for key in social),
            evidence_url=PAGE,
        )
    audit = WebsiteAudit(
        business_id=business.id,
        url_audited=PAGE,
        final_url=PAGE,
        status=AuditStatus.done,
        checks=as_payload(checks),
        tech_stack={"platforms": []},
        findings=[],
        rules_version="audit-2",
        created_at=NOW,
    )
    session.add(audit)
    session.flush()
    return audit


def make_opportunity(
    session: Session,
    business: Business,
    *,
    service: str = "website_design",
    status: ReviewStatus = ReviewStatus.pending,
    score: float = 0.7,
    assigned_to: uuid.UUID | None = None,
    decided_by: uuid.UUID | None = None,
    decided_at: datetime | None = None,
) -> Opportunity:
    row = Opportunity(
        business_id=business.id,
        service=service,
        source=OpportunitySource.rules,
        reason="Audit found the site is served over http.",
        evidence=[],
        confidence=Decimal("0.8"),
        score=Decimal(str(score)),
        score_components={"facts": 0.5, "inference": 0.8, "intent": 0.0, "contactability": 1.0},
        scoring_version="scoring-1",
        review_status=status,
        assigned_to=assigned_to,
        decided_by=decided_by,
        decided_at=decided_at,
    )
    session.add(row)
    session.flush()
    return row


def approve(
    session: Session, business: Business, reviewer: User, *, service: str = "website_design"
) -> Opportunity:
    return make_opportunity(
        session,
        business,
        service=service,
        status=ReviewStatus.approved,
        decided_by=reviewer.id,
        decided_at=NOW,
    )


def get(client: TestClient, user: User, path: str, **params: Any) -> Any:
    response = client.get(path, params=params, headers=auth_headers(client, user))
    assert response.status_code == 200, response.text
    return response.json()


# --- the source block on review detail and lead detail ------------------------------------


def test_review_detail_names_the_source_and_links_to_the_record(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    business = make_business(db)
    make_record(
        db,
        places,
        business,
        record_id="ChIJplaces1",
        first_discovered_at=NOW - timedelta(days=4),
        last_discovered_at=NOW,
    )
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")

    assert len(body["sources"]) == 1
    entry = body["sources"][0]
    assert entry["code"] == "google_places"
    assert entry["name"] == "Google Places API (New)"
    assert entry["source_record_id"] == "ChIJplaces1"
    assert entry["source_url"] == "https://maps.invalid/ChIJplaces1"
    # First found, and last seen again — two different stamps, both on the page.
    assert entry["discovered_at"].startswith("2026-09-18")
    assert entry["last_seen_at"].startswith("2026-09-22")


def test_a_business_built_from_two_records_shows_both(
    client: TestClient, db: Session, places: Source, demo: Source, reviewer: User
) -> None:
    """Not just the survivorship winner: the business answers for every record behind it."""
    business = make_business(db)
    make_record(
        db, places, business, record_id="ChIJplaces1", first_discovered_at=NOW - timedelta(days=9)
    )
    make_record(db, demo, business, record_id="demo-1", first_discovered_at=NOW - timedelta(days=2))
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")

    # Earliest first, so the page reads as a history.
    assert [entry["code"] for entry in body["sources"]] == ["google_places", "demo_fixture"]
    assert [entry["source_record_id"] for entry in body["sources"]] == ["ChIJplaces1", "demo-1"]


def test_a_record_without_a_source_url_is_listed_without_one(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    """Unknown is null, never a guessed link."""
    business = make_business(db)
    make_record(db, places, business, source_url=None)
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")

    assert body["sources"][0]["source_url"] is None


def test_lead_detail_carries_the_same_block(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    business = make_business(db)
    make_record(db, places, business, record_id="ChIJplaces1")
    opportunity = approve(db, business, reviewer)
    db.commit()

    body = get(client, reviewer, f"/api/v1/leads/{opportunity.id}")

    assert [entry["code"] for entry in body["sources"]] == ["google_places"]
    assert body["sources"][0]["source_record_id"] == "ChIJplaces1"


# --- profiles the business's own homepage links to ----------------------------------------


def test_homepage_linked_profiles_are_shown_with_the_page_they_were_read_on(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    business = make_business(db)
    make_record(db, places, business)
    make_audit(db, business, social=["facebook", "instagram"])
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")

    block = body["linked_profiles"]
    assert [p["platform"] for p in block["profiles"]] == ["facebook", "instagram"]
    assert block["profiles"][0]["url"] == "https://www.facebook.invalid/barton"
    # The homepage, which is what lets the screen say these are *their* links.
    assert block["page_url"] == PAGE


def test_a_business_whose_homepage_links_nowhere_has_an_empty_block(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    business = make_business(db)
    make_record(db, places, business)
    make_audit(db, business, social=[])
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")

    assert body["linked_profiles"] == {"page_url": None, "profiles": []}


def test_an_unaudited_business_has_an_empty_profiles_block(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    business = make_business(db)
    make_record(db, places, business)
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")

    assert body["linked_profiles"]["profiles"] == []


# --- the source column on both lists ------------------------------------------------------


def test_the_queue_lists_the_sources_behind_each_business(
    client: TestClient, db: Session, places: Source, demo: Source, reviewer: User
) -> None:
    business = make_business(db)
    make_record(db, places, business)
    make_record(db, demo, business)
    make_record(db, places, business)  # a second Places record: still one source
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, "/api/v1/review-queue")

    assert body["items"][0]["sources"] == ["demo_fixture", "google_places"]


def test_the_leads_list_carries_the_source_column(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    business = make_business(db)
    make_record(db, places, business)
    approve(db, business, reviewer)
    db.commit()

    body = get(client, reviewer, "/api/v1/leads")

    assert body["items"][0]["sources"] == ["google_places"]


# --- "found within" -----------------------------------------------------------------------


@pytest.fixture
def three_businesses(db: Session, places: Source, reviewer: User) -> dict[str, Business]:
    """Found today, four days ago and forty days ago, all otherwise identical."""
    ages = {"today": 0, "four_days": 4, "forty_days": 40}
    built: dict[str, Business] = {}
    for key, days in ages.items():
        business = make_business(db, name=f"Plumbing {key}")
        make_record(db, places, business, first_discovered_at=NOW - timedelta(days=days))
        make_opportunity(db, business, score=0.9 - days / 100)
        built[key] = business
    db.commit()
    return built


def names(body: Any) -> set[str]:
    return {item.get("display_name") or item["business_name"] for item in body["items"]}


def test_found_within_narrows_the_queue_to_recently_discovered_businesses(
    client: TestClient, reviewer: User, three_businesses: dict[str, Business]
) -> None:
    assert names(get(client, reviewer, "/api/v1/review-queue")) == {
        "Plumbing today",
        "Plumbing four_days",
        "Plumbing forty_days",
    }
    assert names(get(client, reviewer, "/api/v1/review-queue", discovered_within_days=1)) == {
        "Plumbing today"
    }
    assert names(get(client, reviewer, "/api/v1/review-queue", discovered_within_days=7)) == {
        "Plumbing today",
        "Plumbing four_days",
    }
    assert names(get(client, reviewer, "/api/v1/review-queue", discovered_within_days=30)) == {
        "Plumbing today",
        "Plumbing four_days",
    }


def test_found_within_is_first_found_not_last_seen(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    """A record the source handed over again this morning does not make the business new."""
    business = make_business(db, name="Long-known Plumbing")
    make_record(
        db,
        places,
        business,
        first_discovered_at=NOW - timedelta(days=200),
        last_discovered_at=NOW,
    )
    make_opportunity(db, business)
    db.commit()

    assert names(get(client, reviewer, "/api/v1/review-queue")) == {"Long-known Plumbing"}
    assert get(client, reviewer, "/api/v1/review-queue", discovered_within_days=1)["items"] == []


def test_the_earliest_record_decides_for_a_business_with_several(
    client: TestClient, db: Session, places: Source, demo: Source, reviewer: User
) -> None:
    business = make_business(db, name="Two-record Plumbing")
    make_record(db, places, business, first_discovered_at=NOW - timedelta(days=100))
    make_record(db, demo, business, first_discovered_at=NOW)
    make_opportunity(db, business)
    db.commit()

    # Found 100 days ago, whatever the newer record says: "first found" is the earliest one.
    assert get(client, reviewer, "/api/v1/review-queue", discovered_within_days=1)["items"] == []
    assert names(get(client, reviewer, "/api/v1/review-queue", discovered_within_days=365)) == {
        "Two-record Plumbing"
    }


def test_found_within_composes_with_the_other_filters(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    """Three filters at once, each of which alone would let a different business through."""
    wanted = make_business(db, name="Austin Recent Plumbing", city="Austin", industry="plumbing")
    make_record(db, places, wanted, first_discovered_at=NOW)
    make_opportunity(db, wanted, service="website_design", score=0.9)

    wrong_city = make_business(db, name="Houston Recent", city="Houston", industry="plumbing")
    make_record(db, places, wrong_city, first_discovered_at=NOW)
    make_opportunity(db, wrong_city, service="website_design", score=0.9)

    wrong_service = make_business(db, name="Austin Recent SEO", city="Austin")
    make_record(db, places, wrong_service, first_discovered_at=NOW)
    make_opportunity(db, wrong_service, service="seo_gbp", score=0.9)

    too_old = make_business(db, name="Austin Old Plumbing", city="Austin")
    make_record(db, places, too_old, first_discovered_at=NOW - timedelta(days=60))
    make_opportunity(db, too_old, service="website_design", score=0.9)
    db.commit()

    body = get(
        client,
        reviewer,
        "/api/v1/review-queue",
        city="Austin",
        service="website_design",
        min_score=0.5,
        discovered_within_days=7,
    )

    assert names(body) == {"Austin Recent Plumbing"}


def test_found_within_holds_across_a_page_boundary(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    """Two recent businesses and one old one, one per page: the old one never appears."""
    for index, days in enumerate((0, 1, 90)):
        business = make_business(db, name=f"Paged {days}", city="Austin")
        make_record(db, places, business, first_discovered_at=NOW - timedelta(days=days))
        make_opportunity(db, business, score=0.9 - index / 10)
    db.commit()

    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {
            "city": "Austin",
            "min_score": 0.1,
            "discovered_within_days": 7,
            "limit": 1,
        }
        if cursor:
            params["cursor"] = cursor
        body = get(client, reviewer, "/api/v1/review-queue", **params)
        pages += 1
        seen |= names(body)
        cursor = body["next_cursor"]
        if not cursor:
            break
        assert pages < 5, "the cursor is not terminating"

    assert seen == {"Paged 0", "Paged 1"}
    assert pages == 2, "one business per page, and the old one is never a third page"


def test_found_within_narrows_the_leads_list_too(
    client: TestClient, db: Session, places: Source, reviewer: User
) -> None:
    recent = make_business(db, name="Recent Lead")
    make_record(db, places, recent, first_discovered_at=NOW)
    approve(db, recent, reviewer)

    old = make_business(db, name="Old Lead")
    make_record(db, places, old, first_discovered_at=NOW - timedelta(days=90))
    approve(db, old, reviewer)
    db.commit()

    assert names(get(client, reviewer, "/api/v1/leads")) == {"Recent Lead", "Old Lead"}
    assert names(get(client, reviewer, "/api/v1/leads", discovered_within_days=7)) == {
        "Recent Lead"
    }


def test_found_within_composes_with_the_leads_filters_and_paginates(
    client: TestClient, db: Session, places: Source, reviewer: User, rep: User
) -> None:
    for index, days in enumerate((0, 1, 90)):
        business = make_business(db, name=f"Lead {days}", city="Austin")
        make_record(db, places, business, first_discovered_at=NOW - timedelta(days=days))
        make_opportunity(
            db,
            business,
            status=ReviewStatus.approved,
            decided_by=reviewer.id,
            decided_at=NOW - timedelta(minutes=index),
            assigned_to=rep.id,
        )
    other = make_business(db, name="Lead elsewhere", city="Houston")
    make_record(db, places, other, first_discovered_at=NOW)
    make_opportunity(
        db,
        other,
        status=ReviewStatus.approved,
        decided_by=reviewer.id,
        decided_at=NOW,
        assigned_to=rep.id,
    )
    db.commit()

    seen: set[str] = set()
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "city": "Austin",
            "assigned_to": str(rep.id),
            "discovered_within_days": 7,
            "limit": 1,
        }
        if cursor:
            params["cursor"] = cursor
        body = get(client, reviewer, "/api/v1/leads", **params)
        seen |= names(body)
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert seen == {"Lead 0", "Lead 1"}


def test_a_business_with_no_discovered_record_is_not_within_any_window(
    client: TestClient, db: Session, reviewer: User
) -> None:
    """ "First found within N days" cannot be true of something never discovered."""
    business = make_business(db, name="No records")
    make_opportunity(db, business)
    db.commit()

    assert names(get(client, reviewer, "/api/v1/review-queue")) == {"No records"}
    assert get(client, reviewer, "/api/v1/review-queue", discovered_within_days=3650)["items"] == []


@pytest.mark.parametrize("value", [0, -1, 4000])
def test_an_out_of_range_window_is_rejected(client: TestClient, reviewer: User, value: int) -> None:
    response = client.get(
        "/api/v1/review-queue",
        params={"discovered_within_days": value},
        headers=auth_headers(client, reviewer),
    )

    assert response.status_code == 422


# --- the AI switch, as the pages see it ---------------------------------------------------


def test_review_detail_says_whether_the_ai_layer_is_on(
    client: TestClient,
    db: Session,
    places: Source,
    reviewer: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    business = make_business(db)
    make_record(db, places, business)
    make_opportunity(db, business)
    db.commit()

    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")
    assert body["ai_enabled"] is True, "the suite runs with the fake provider"

    monkeypatch.setenv("AI_PROVIDER", "disabled")
    get_settings.cache_clear()
    body = get(client, reviewer, f"/api/v1/review-queue/{business.id}")
    assert body["ai_enabled"] is False
    assert body["ai"] is None


def test_lead_detail_says_whether_the_ai_layer_is_on(
    client: TestClient,
    db: Session,
    places: Source,
    reviewer: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    business = make_business(db)
    make_record(db, places, business)
    opportunity = approve(db, business, reviewer)
    db.commit()

    monkeypatch.setenv("AI_PROVIDER", "disabled")
    get_settings.cache_clear()

    body = get(client, reviewer, f"/api/v1/leads/{opportunity.id}")

    assert body["ai_enabled"] is False
