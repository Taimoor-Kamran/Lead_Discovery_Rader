"""The review queue: who may see it, who may decide, and what a decision does.

This is the human gate the blueprint asks for. The tests that matter most are the ones
proving a machine never crosses it: a pending pair creates no business, and a role that
is not a reviewer cannot decide.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.resolution import service as resolution
from app.modules.resolution.models import MatchCandidate, MatchCandidateStatus, ResolutionStatus
from app.modules.sources.models import Source, SourceKind
from tests.conftest import auth_headers, make_user

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def place(
    place_id: str,
    name: str,
    *,
    number: str,
    postal: str,
    lat: float,
    lng: float,
    phone: str,
    website: str | None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "id": place_id,
        "displayName": {"text": name},
        "formattedAddress": f"{number} Main St, Austin, TX {postal}, USA",
        "addressComponents": [
            {"longText": number, "shortText": number, "types": ["street_number"]},
            {"longText": "Main Street", "shortText": "Main St", "types": ["route"]},
            {"longText": "Austin", "shortText": "Austin", "types": ["locality"]},
            {"longText": "Texas", "shortText": "TX", "types": ["administrative_area_level_1"]},
            {"longText": postal, "shortText": postal, "types": ["postal_code"]},
            {"longText": "United States", "shortText": "US", "types": ["country"]},
        ],
        "location": {"latitude": lat, "longitude": lng},
        "nationalPhoneNumber": phone,
        "businessStatus": "OPERATIONAL",
        "types": ["plumber"],
        "primaryType": "plumber",
    }
    if website:
        body["websiteUri"] = website
    return body


@pytest.fixture
def queue(db: Session) -> Iterator[tuple[DiscoveredRecord, MatchCandidate]]:
    """One near-duplicate pair: a business, and a record waiting on a human for it."""
    source = Source(name="google_places", kind=SourceKind.api, config={}, enabled=True)
    db.add(source)
    db.flush()

    payloads = [
        place(
            "ChIJfirst",
            "ABC Plumbing LLC",
            number="600",
            postal="78703",
            lat=30.3640,
            lng=-97.7450,
            phone="(512) 555-0201",
            website="https://abcplumbing.invalid/",
        ),
        place(
            "ChIJsecond",
            "ABC Plumbing & HVAC",
            number="610",
            postal="78703",
            lat=30.3649,
            lng=-97.7450,
            phone="(512) 555-0202",
            website=None,
        ),
    ]
    records = []
    for payload in payloads:
        record = DiscoveredRecord(
            source_id=source.id,
            source_record_id=str(payload["id"]),
            raw_payload=payload,
            first_discovered_at=NOW,
            last_discovered_at=NOW,
            content_expires_at=NOW + timedelta(days=30),
        )
        db.add(record)
        db.flush()
        records.append(record)

    for record in records:
        resolution.resolve_record(db, record, now=NOW)
    db.commit()

    candidate = db.scalars(
        select(MatchCandidate).where(MatchCandidate.discovered_record_id == records[1].id)
    ).one()
    assert candidate.status is MatchCandidateStatus.pending
    yield records[1], candidate


# --- reading ------------------------------------------------------------------------


def test_a_reviewer_sees_both_sides_and_every_signal(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    reviewer = make_user(db, Role.reviewer)

    response = client.get(
        "/api/v1/match-candidates?status=pending", headers=auth_headers(client, reviewer)
    )

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["record"]["source_record_id"] == "ChIJsecond"
    assert item["business"]["display_name"] == "ABC Plumbing LLC"
    assert set(item["signals"]) == {
        "domain_match",
        "phone_match",
        "name_similarity",
        "address_match",
        "geo_proximity",
    }
    assert float(item["score"]) > 0


@pytest.mark.parametrize("role", [Role.sales_rep, Role.crm_manager])
def test_a_role_that_does_not_review_cannot_see_the_queue(
    client: TestClient, db: Session, role: Role, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    user = make_user(db, role)

    response = client.get("/api/v1/match-candidates", headers=auth_headers(client, user))

    assert response.status_code == 403


@pytest.mark.parametrize("role", [Role.admin, Role.reviewer])
def test_reviewers_and_admins_may_read_the_queue(
    client: TestClient, db: Session, role: Role, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    user = make_user(db, role)

    assert (
        client.get("/api/v1/match-candidates", headers=auth_headers(client, user)).status_code
        == 200
    )


# --- deciding -----------------------------------------------------------------------


def test_a_sales_rep_is_forbidden_from_deciding(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    """The acceptance criterion: `sales_rep` gets 403 on a decision."""
    _, candidate = queue
    rep = make_user(db, Role.sales_rep)

    response = client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": "merge"},
        headers=auth_headers(client, rep),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"

    db.expire_all()
    untouched = db.get(MatchCandidate, candidate.id)
    assert untouched is not None
    assert untouched.status is MatchCandidateStatus.pending


def test_deciding_requires_authentication(
    client: TestClient, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    _, candidate = queue

    response = client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision", json={"decision": "merge"}
    )

    assert response.status_code == 401


def test_merge_links_the_record_to_that_business(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    record, candidate = queue
    reviewer = make_user(db, Role.reviewer)
    businesses_before = len(list(db.scalars(select(Business))))

    response = client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": "merge"},
        headers=auth_headers(client, reviewer),
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    decided = db.get(MatchCandidate, candidate.id)
    linked = db.get(DiscoveredRecord, record.id)
    assert decided is not None and linked is not None
    assert decided.status is MatchCandidateStatus.merged
    assert decided.decided_by == reviewer.id
    assert decided.decided_at is not None
    assert linked.business_id == candidate.business_id
    assert linked.resolution_status is ResolutionStatus.linked
    assert len(list(db.scalars(select(Business)))) == businesses_before, "no new business"


def test_merge_writes_the_records_values_onto_the_business(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    record, candidate = queue
    reviewer = make_user(db, Role.reviewer)

    client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": "merge"},
        headers=auth_headers(client, reviewer),
    )
    db.expire_all()

    detail = client.get(
        f"/api/v1/businesses/{candidate.business_id}", headers=auth_headers(client, reviewer)
    ).json()
    sources = {v["discovered_record_id"] for v in detail["field_values"]}
    assert str(record.id) in sources, "what the merged record said is now on the business"
    assert len(detail["records"]) == 2


def test_keep_apart_on_the_last_candidate_creates_a_business(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    record, candidate = queue
    reviewer = make_user(db, Role.reviewer)
    businesses_before = len(list(db.scalars(select(Business))))

    response = client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": "keep_apart"},
        headers=auth_headers(client, reviewer),
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    decided = db.get(MatchCandidate, candidate.id)
    linked = db.get(DiscoveredRecord, record.id)
    assert decided is not None and linked is not None
    assert decided.status is MatchCandidateStatus.kept_apart
    assert linked.resolution_status is ResolutionStatus.linked
    assert linked.business_id is not None
    assert linked.business_id != candidate.business_id
    assert len(list(db.scalars(select(Business)))) == businesses_before + 1


def test_merge_closes_the_records_other_pending_candidates(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    record, candidate = queue
    reviewer = make_user(db, Role.reviewer)
    # A second plausible business for the same record.
    other = Business(display_name="ABC Plumbing Services", normalized_name="abc plumbing services")
    db.add(other)
    db.flush()
    sibling = MatchCandidate(
        discovered_record_id=record.id,
        business_id=other.id,
        score=Decimal("0.610"),
        signals={},
        status=MatchCandidateStatus.pending,
    )
    db.add(sibling)
    db.commit()

    client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": "merge"},
        headers=auth_headers(client, reviewer),
    )
    db.expire_all()

    sibling_after = db.get(MatchCandidate, sibling.id)
    assert sibling_after is not None
    assert sibling_after.status is MatchCandidateStatus.kept_apart


def test_keep_apart_while_another_candidate_is_pending_creates_nothing(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    record, candidate = queue
    reviewer = make_user(db, Role.reviewer)
    other = Business(display_name="ABC Plumbing Services", normalized_name="abc plumbing services")
    db.add(other)
    db.flush()
    db.add(
        MatchCandidate(
            discovered_record_id=record.id,
            business_id=other.id,
            score=Decimal("0.610"),
            signals={},
            status=MatchCandidateStatus.pending,
        )
    )
    db.commit()
    businesses_before = len(list(db.scalars(select(Business))))

    client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": "keep_apart"},
        headers=auth_headers(client, reviewer),
    )
    db.expire_all()

    assert len(list(db.scalars(select(Business)))) == businesses_before
    unchanged = db.get(DiscoveredRecord, record.id)
    assert unchanged is not None
    assert unchanged.business_id is None


@pytest.mark.parametrize("decision", ["merge", "keep_apart"])
def test_every_decision_writes_an_audit_entry(
    client: TestClient, db: Session, decision: str, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    record, candidate = queue
    reviewer = make_user(db, Role.reviewer)

    client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": decision},
        headers=auth_headers(client, reviewer),
    )

    entry = db.scalars(
        select(AuditLog)
        .where(AuditLog.action == "match_candidate.decided")
        .order_by(AuditLog.id.desc())
    ).first()
    assert entry is not None
    assert entry.actor_id == reviewer.id
    assert entry.entity_id == str(candidate.id)
    assert entry.after is not None
    assert entry.after["decision"] == decision
    assert entry.after["discovered_record_id"] == str(record.id)


def test_deciding_twice_is_a_conflict(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    _, candidate = queue
    reviewer = make_user(db, Role.reviewer)
    headers = auth_headers(client, reviewer)
    body = {"decision": "merge"}

    assert (
        client.post(
            f"/api/v1/match-candidates/{candidate.id}/decision", json=body, headers=headers
        ).status_code
        == 200
    )
    second = client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision", json=body, headers=headers
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_an_unknown_decision_is_rejected(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    _, candidate = queue
    reviewer = make_user(db, Role.reviewer)

    response = client.post(
        f"/api/v1/match-candidates/{candidate.id}/decision",
        json={"decision": "obliterate"},
        headers=auth_headers(client, reviewer),
    )

    assert response.status_code == 422


def test_an_unknown_candidate_is_404(client: TestClient, db: Session) -> None:
    reviewer = make_user(db, Role.reviewer)

    response = client.post(
        f"/api/v1/match-candidates/{uuid.uuid4()}/decision",
        json={"decision": "merge"},
        headers=auth_headers(client, reviewer),
    )

    assert response.status_code == 404


# --- POST /jobs/{id}/resolve --------------------------------------------------------


def test_a_tech_admin_may_resolve_a_discovery_run_again(
    client: TestClient, db: Session, queue: tuple[DiscoveredRecord, MatchCandidate]
) -> None:
    from app.modules.jobs.service import DISCOVERY_JOB_KIND, enqueue_run

    operator = make_user(db, Role.tech_admin)
    run = enqueue_run(db, search_job_id=None, kind=DISCOVERY_JOB_KIND)
    db.commit()

    response = client.post(
        f"/api/v1/jobs/{run.id}/resolve",
        headers={**auth_headers(client, operator), "Idempotency-Key": "manual-1"},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "resolution"
    assert body["status"] == JobRunStatus.queued.value

    again = client.post(
        f"/api/v1/jobs/{run.id}/resolve",
        headers={**auth_headers(client, operator), "Idempotency-Key": "manual-1"},
    )
    assert again.json()["id"] == body["id"], "the same key returns the same run"


def test_a_sales_rep_may_not_resolve_a_run(client: TestClient, db: Session) -> None:
    from app.modules.jobs.service import DISCOVERY_JOB_KIND, enqueue_run

    rep = make_user(db, Role.sales_rep)
    run = enqueue_run(db, search_job_id=None, kind=DISCOVERY_JOB_KIND)
    db.commit()

    response = client.post(f"/api/v1/jobs/{run.id}/resolve", headers=auth_headers(client, rep))

    assert response.status_code == 403


def test_only_a_discovery_run_can_be_resolved(client: TestClient, db: Session) -> None:
    operator = make_user(db, Role.tech_admin)
    run = JobRun(search_job_id=None, kind="resolution", status=JobRunStatus.done)
    db.add(run)
    db.commit()

    response = client.post(f"/api/v1/jobs/{run.id}/resolve", headers=auth_headers(client, operator))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
