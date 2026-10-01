"""v0.14.0: the queue's two sorts, their keyset cursors, the new row fields and the F9 badges.

The seeded set has deliberate ties (equal scores, equal review counts) and nulls (no review
count), so the cursor is exercised where it breaks rather than assumed to work.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailedError
from app.modules.audit_web.models import AuditStatus
from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.opportunities import service as opportunities
from app.modules.review import service as review
from app.modules.review.schemas import QueueBadge, QueueSort, SortDir
from tests.conftest import auth_headers, make_user
from tests.factories import finding
from tests.integration.test_classification_run import (
    NOW,
    URL,
    make_audit,
    make_business,
    make_tools,
)
from tests.integration.test_review_api import make_opportunity

# (name, top score, review count, also a weak opportunity?)
SEED: list[tuple[str, float, int | None, bool]] = [
    ("A", 0.9, 40, False),
    ("B", 0.7, 40, True),
    ("C", 0.7, None, False),
    ("D", 0.7, 12, True),
    ("E", 0.5, None, True),
    ("F", 0.5, 300, False),
    ("G", 0.3, 0, False),
    ("H", 0.3, None, False),
    ("I", 0.3, 12, True),
]


@pytest.fixture
def seeded(db: Session) -> dict[str, Business]:
    rows: dict[str, Business] = {}
    for name, score, reviews, weak in SEED:
        business = make_business(db, name=name)
        business.user_rating_count = reviews
        make_opportunity(db, business, "website_design", score=score)
        if weak:
            make_opportunity(db, business, "ads_social", confidence=0.2, score=0.99)
        rows[name] = business
    db.commit()
    return rows


def expected(sort: QueueSort, sort_dir: SortDir, seeded: dict[str, Business]) -> list[str]:
    """The order worked out independently of the SQL: nulls last, ties on business id."""
    descending = sort_dir is SortDir.desc
    present: list[tuple[Any, Any, str]] = []
    missing: list[tuple[Any, str]] = []
    for name, score, reviews, _ in SEED:
        value = score if sort is QueueSort.score else reviews
        if value is None:
            missing.append((seeded[name].id, name))
        else:
            present.append((value, seeded[name].id, name))
    present.sort(reverse=descending)
    missing.sort(reverse=descending)
    return [row[-1] for row in present] + [row[-1] for row in missing]


def page_through(db: Session, *, limit: int, **params: Any) -> list[list[Any]]:
    pages: list[list[Any]] = []
    cursor = None
    while True:
        page = review.review_queue(db, limit=limit, cursor=cursor, **params)
        pages.append(page.items)
        cursor = page.next_cursor
        if cursor is None:
            return pages


@pytest.mark.parametrize("sort", list(QueueSort))
@pytest.mark.parametrize("sort_dir", list(SortDir))
@pytest.mark.parametrize("limit", [1, 2, 4, 100])
def test_each_sort_pages_through_every_row_exactly_once_in_order(
    db: Session, seeded: dict[str, Business], sort: QueueSort, sort_dir: SortDir, limit: int
) -> None:
    pages = page_through(db, limit=limit, sort=sort, sort_dir=sort_dir)
    names = [item.display_name for page in pages for item in page]
    assert names == expected(sort, sort_dir, seeded)
    assert len(names) == len(set(names)) == len(SEED), "no row repeated, none missing"


@pytest.mark.parametrize("sort_dir", list(SortDir))
def test_no_review_count_sorts_last_in_both_directions(
    db: Session, seeded: dict[str, Business], sort_dir: SortDir
) -> None:
    items = review.review_queue(db, sort=QueueSort.reviews, sort_dir=sort_dir).items
    counts = [item.user_rating_count for item in items]
    assert counts[-3:] == [None, None, None]
    assert None not in counts[:-3]
    assert 0 in counts, "a measured zero is a count, not a missing one"


def test_score_desc_is_still_the_default_order(db: Session, seeded: dict[str, Business]) -> None:
    items = review.review_queue(db).items
    assert [i.display_name for i in items] == expected(QueueSort.score, SortDir.desc, seeded)


def test_weak_counts_are_identical_under_both_sorts_and_directions(
    db: Session, seeded: dict[str, Business]
) -> None:
    def hidden(**params: Any) -> dict[str, int]:
        items = [i for page in page_through(db, limit=2, **params) for i in page]
        return {i.display_name: i.weak_hidden for i in items}

    baseline = hidden()
    assert sum(baseline.values()) == 4
    for sort in QueueSort:
        for sort_dir in SortDir:
            assert hidden(sort=sort, sort_dir=sort_dir) == baseline
            shown = {
                i.display_name
                for page in page_through(
                    db, limit=3, sort=sort, sort_dir=sort_dir, include_weak=True
                )
                for i in page
            }
            assert shown == {name for name, *_ in SEED}


def test_a_cursor_only_continues_the_sort_it_came_from(
    db: Session, seeded: dict[str, Business]
) -> None:
    cursor = review.review_queue(db, limit=1, sort=QueueSort.reviews).next_cursor
    assert cursor is not None

    with pytest.raises(ValidationFailedError):
        review.review_queue(db, limit=1, cursor=cursor)
    with pytest.raises(ValidationFailedError):
        review.review_queue(
            db, limit=1, cursor=cursor, sort=QueueSort.reviews, sort_dir=SortDir.asc
        )


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer, "reviewer@example.com")


def test_api_accepts_the_two_sorts_and_refuses_anything_else(
    client: TestClient, db: Session, seeded: dict[str, Business], reviewer: User
) -> None:
    headers = auth_headers(client, reviewer)

    def get(**params: Any) -> Any:
        return client.get("/api/v1/review-queue", params=params, headers=headers)

    ok = get(sort="reviews", sort_dir="asc", limit=2)
    assert ok.status_code == 200, ok.text
    assert [i["display_name"] for i in ok.json()["items"]] == expected(
        QueueSort.reviews, SortDir.asc, seeded
    )[:2]
    nxt = get(sort="reviews", sort_dir="asc", limit=2, cursor=ok.json()["next_cursor"])
    assert nxt.status_code == 200
    assert get(sort="pagespeed").status_code == 422
    assert get(sort_dir="up").status_code == 422
    assert get(cursor=ok.json()["next_cursor"]).status_code == 422


# --- row fields ---------------------------------------------------------------------------


def test_row_carries_pagespeed_finding_count_and_website_kind(db: Session) -> None:
    measured = make_business(db, name="Measured")
    audit = make_audit(
        db,
        measured,
        findings=[finding("no_online_booking", url=URL), finding("no_https", url=URL)],
    )
    audit.psi = {"performance_score": 41, "strategy": "mobile"}
    make_opportunity(db, measured, score=0.6)
    unmeasured = make_business(db, name="Unmeasured")
    make_audit(db, unmeasured, findings=[], page_text=None, status=AuditStatus.unreachable)
    make_opportunity(db, unmeasured, score=0.6)
    db.commit()

    rows = {i.display_name: i for i in review.review_queue(db).items}
    got = rows["Measured"].latest_audit
    assert got is not None and (got.pagespeed_score, got.finding_count) == (41, 2)
    assert rows["Measured"].website_kind is WebsiteKind.own_site
    nothing = rows["Unmeasured"].latest_audit
    assert nothing is not None
    assert nothing.pagespeed_score is None, "PageSpeed not run is unknown, never 0"
    assert nothing.finding_count is None, "a page never read has no count, not zero"


def test_a_clean_audit_that_read_the_page_counts_zero_findings(db: Session) -> None:
    clean = make_business(db, name="Clean")
    make_audit(db, clean, findings=[])
    make_opportunity(db, clean)
    db.commit()
    [item] = review.review_queue(db).items
    assert item.latest_audit is not None and item.latest_audit.finding_count == 0


# --- badges -------------------------------------------------------------------------------


def test_badges_come_from_the_listing_and_filter_the_queue(db: Session) -> None:
    normal = make_business(db, name="Normal")
    no_site = make_business(db, name="No Site")
    no_site.website, no_site.website_kind = None, WebsiteKind.none
    social = make_business(db, name="Social Only")
    social.website_kind = WebsiteKind.social_profile
    for business in (normal, no_site, social):
        make_opportunity(db, business)
    db.commit()

    rows = {i.display_name: i.badges for i in review.review_queue(db).items}
    assert rows == {"Normal": [], "No Site": [QueueBadge.no_website], "Social Only": []}
    filtered = review.review_queue(db, badge=QueueBadge.no_website).items
    assert [i.display_name for i in filtered] == ["No Site"]
    assert review.review_queue(db, badge=QueueBadge.closed_permanently).items == []


def test_the_closed_badge_never_appears_on_a_row_carrying_an_opportunity(db: Session) -> None:
    """The pipeline never leaves a permanently closed business with an open opportunity —
    new ones are not made and old ones are withdrawn — so no queue row carries the badge."""
    gone = make_business(db, name="Gone")
    make_audit(db, gone)
    opportunities.classify(db, gone, tools=make_tools("not json"), now=NOW)
    assert [i.display_name for i in review.review_queue(db).items] == ["Gone"]

    gone.business_status = BusinessStatus.closed_permanently
    opportunities.classify(db, gone, tools=make_tools("not json"), now=NOW)
    never = make_business(db, name="Never Open")
    never.business_status = BusinessStatus.closed_permanently
    make_audit(db, never)
    opportunities.classify(db, never, tools=make_tools("not json"), now=NOW)
    db.commit()

    for sort in QueueSort:
        for weak in (False, True):
            items = review.review_queue(db, sort=sort, include_weak=weak).items
            assert all(QueueBadge.closed_permanently not in i.badges for i in items)
            assert items == []
    assert review.review_queue(db, badge=QueueBadge.closed_permanently).items == []
