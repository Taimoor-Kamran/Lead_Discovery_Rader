"""The `resolution` job: how it is queued, what it processes, and what it never does twice.

Two properties are load-bearing. Discovery must hand off to resolution by itself, or raw
records simply pile up; and two records of one new business seen in a single run must
produce one business, not two.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.adapters.google_places.client import PLACES_BASE_URL, TEXT_SEARCH_PATH
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.businesses.models import Business, BusinessFieldValue
from app.modules.discovery.models import DiscoveredRecord, RecordSighting
from app.modules.jobs.models import JobRun, JobRunStatus, SearchJob, SearchJobStatus
from app.modules.jobs.service import DISCOVERY_JOB_KIND, RESOLUTION_JOB_KIND, enqueue_run
from app.modules.resolution import service as resolution
from app.modules.resolution.models import ResolutionStatus
from app.modules.resolution.worker import parent_run_id
from app.modules.sources.models import Source, SourceKind
from app.workers import tasks

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
SEARCH_URL = f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}"


@pytest.fixture
def source(db: Session) -> Source:
    row = Source(name="google_places", kind=SourceKind.api, config={}, enabled=True)
    db.add(row)
    db.flush()
    return row


def payload(
    place_id: str,
    name: str,
    *,
    phone: str,
    website: str | None = None,
    number: str = "123",
    postal: str = "78701",
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
        "location": {"latitude": 30.2672, "longitude": -97.7431},
        "nationalPhoneNumber": phone,
        "businessStatus": "OPERATIONAL",
        "types": ["plumber"],
        "primaryType": "plumber",
    }
    if website:
        body["websiteUri"] = website
    return body


def store(db: Session, source: Source, run: JobRun, payloads: list[dict[str, object]]) -> None:
    """Write records and their sightings the way a discovery run does."""
    for rank, body in enumerate(payloads):
        record = DiscoveredRecord(
            source_id=source.id,
            source_record_id=str(body["id"]),
            raw_payload=body,
            first_discovered_at=NOW,
            last_discovered_at=NOW,
            content_expires_at=NOW + timedelta(days=30),
        )
        db.add(record)
        db.flush()
        db.add(
            RecordSighting(
                discovered_record_id=record.id,
                job_run_id=run.id,
                search_job_id=run.search_job_id,
                seen_at=NOW,
                rank=rank,
            )
        )
    db.flush()


@pytest.fixture
def discovery_run(db: Session, source: Source) -> JobRun:
    run = JobRun(search_job_id=None, kind=DISCOVERY_JOB_KIND, status=JobRunStatus.done)
    db.add(run)
    db.flush()
    return run


def resolve(db: Session, discovery_run: JobRun) -> JobRun:
    run = resolution.enqueue_resolution(db, discovery_run.id)
    db.commit()
    assert tasks.execute_job_run(run.id) is JobRunStatus.done
    db.expire_all()
    refreshed = db.get(JobRun, run.id)
    assert refreshed is not None
    return refreshed


# --- the handoff from discovery ------------------------------------------------------


@pytest.fixture
def places_source(db: Session, places_adapter: GooglePlacesAdapter) -> Source:
    sources = registry.sync_sources(db)
    db.commit()
    return next(s for s in sources if s.name == GooglePlacesAdapter.name)


def test_a_finished_discovery_run_queues_its_own_resolution(
    db: Session,
    mock_http: respx.MockRouter,
    places_source: Source,
    sales_user: User,
) -> None:
    from tests.conftest import places_fixture

    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_page_3.json"))
    )
    job = SearchJob(
        name="Austin plumbers",
        geo={"city": "Austin", "state": "TX"},
        industry="plumber",
        source_ids=[places_source.id],
        status=SearchJobStatus.active,
        created_by=sales_user.id,
    )
    db.add(job)
    db.commit()
    discovery = enqueue_run(db, search_job_id=job.id, kind=DISCOVERY_JOB_KIND)
    db.commit()

    assert tasks.execute_job_run(discovery.id) is JobRunStatus.done
    db.expire_all()

    queued = db.scalars(select(JobRun).where(JobRun.kind == RESOLUTION_JOB_KIND)).all()
    assert len(queued) == 1
    assert parent_run_id(queued[0]) == discovery.id
    assert queued[0].search_job_id == job.id


def test_a_failed_discovery_run_queues_nothing(
    db: Session, mock_http: respx.MockRouter, places_source: Source, sales_user: User
) -> None:
    mock_http.post(SEARCH_URL).mock(return_value=httpx.Response(403, json={"error": {}}))
    job = SearchJob(
        name="Austin plumbers",
        geo={"city": "Austin", "state": "TX"},
        industry="plumber",
        source_ids=[places_source.id],
        status=SearchJobStatus.active,
        created_by=sales_user.id,
    )
    db.add(job)
    db.commit()
    discovery = enqueue_run(db, search_job_id=job.id, kind=DISCOVERY_JOB_KIND)
    db.commit()

    assert tasks.execute_job_run(discovery.id) is JobRunStatus.failed
    db.expire_all()

    assert db.scalars(select(JobRun).where(JobRun.kind == RESOLUTION_JOB_KIND)).first() is None


def test_running_the_same_discovery_twice_leaves_one_resolution_run(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    db.commit()

    first = resolution.enqueue_resolution(
        db, discovery_run.id, idempotency_key=f"resolution:{discovery_run.id}"
    )
    second = resolution.enqueue_resolution(
        db, discovery_run.id, idempotency_key=f"resolution:{discovery_run.id}"
    )
    db.commit()

    assert first.id == second.id


def test_a_resolution_run_must_name_its_parent(db: Session) -> None:
    run = JobRun(search_job_id=None, kind=RESOLUTION_JOB_KIND, status=JobRunStatus.queued)
    db.add(run)
    db.commit()

    assert tasks.execute_job_run(run.id) is JobRunStatus.failed
    db.expire_all()
    failed = db.get(JobRun, run.id)
    assert failed is not None and failed.error is not None
    assert "discovery run" in failed.error


# --- what one run does ---------------------------------------------------------------


def test_a_run_reports_what_it_did(db: Session, source: Source, discovery_run: JobRun) -> None:
    store(
        db,
        source,
        discovery_run,
        [
            payload(
                "ChIJone",
                "Lone Star Plumbing",
                phone="(512) 555-0101",
                website="https://lonestar.invalid/",
            ),
            payload(
                "ChIJtwo",
                "Lone Star Plumbing",
                phone="(512) 555-0101",
                website="https://lonestar.invalid/",
            ),
            payload(
                "ChIJthree",
                "Delgado Roofing",
                phone="(512) 555-0199",
                website="https://delgado.invalid/",
                number="9",
                postal="78702",
            ),
        ],
    )
    db.commit()

    run = resolve(db, discovery_run)

    assert run.result_summary == {
        "processed": 3,
        "linked_existing": 1,
        "created": 2,
        "needs_review": 0,
        "invalid": 0,
    }
    assert run.progress_total == 3
    assert run.progress_done == 3


def test_two_records_of_the_same_new_business_in_one_run_produce_one_business(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    """The acceptance criterion the concurrency rule exists for."""
    store(
        db,
        source,
        discovery_run,
        [
            payload(
                "ChIJone",
                "Lone Star Plumbing",
                phone="(512) 555-0101",
                website="https://lonestar.invalid/",
            ),
            payload(
                "ChIJtwo",
                "Lone Star Plumbing Co.",
                phone="(512) 555-0101",
                website="https://lonestar.invalid/",
            ),
        ],
    )
    db.commit()

    resolve(db, discovery_run)

    businesses = list(db.scalars(select(Business)))
    records = list(db.scalars(select(DiscoveredRecord)))
    assert len(businesses) == 1
    assert {r.business_id for r in records} == {businesses[0].id}


def test_a_record_that_cannot_be_normalized_is_marked_invalid_not_dropped(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    nameless = payload("ChIJnameless", "x", phone="(512) 555-0101")
    nameless.pop("displayName")
    store(db, source, discovery_run, [nameless])
    db.commit()

    run = resolve(db, discovery_run)

    record = db.scalars(select(DiscoveredRecord)).one()
    assert run.result_summary is not None
    assert run.result_summary["invalid"] == 1
    assert record.resolution_status is ResolutionStatus.invalid
    assert record.resolution_error
    assert record.business_id is None
    assert list(db.scalars(select(Business))) == []


def test_a_purged_record_says_so_rather_than_resolving_to_nothing(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    store(db, source, discovery_run, [payload("ChIJgone", "Gone", phone="(512) 555-0101")])
    db.commit()
    record = db.scalars(select(DiscoveredRecord)).one()
    record.raw_payload = None
    record.purged_at = NOW
    db.commit()

    resolve(db, discovery_run)

    db.expire_all()
    refreshed = db.scalars(select(DiscoveredRecord)).one()
    assert refreshed.resolution_status is ResolutionStatus.invalid
    assert "purged" in (refreshed.resolution_error or "")


def test_resolution_writes_a_field_value_per_field_with_its_provenance(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    store(db, source, discovery_run, [payload("ChIJone", "Lone Star", phone="(512) 555-0101")])
    db.commit()

    resolve(db, discovery_run)

    record = db.scalars(select(DiscoveredRecord)).one()
    values = list(db.scalars(select(BusinessFieldValue)))
    assert values
    assert {v.discovered_record_id for v in values} == {record.id}
    assert {v.source_id for v in values} == {source.id}
    assert all(v.expires_at == record.content_expires_at for v in values)


def test_a_run_writes_audit_entries_for_its_start_and_finish(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    store(db, source, discovery_run, [payload("ChIJone", "Lone Star", phone="(512) 555-0101")])
    db.commit()

    run = resolve(db, discovery_run)

    actions = [
        entry.action
        for entry in db.scalars(select(AuditLog).where(AuditLog.entity_id == str(run.id)))
    ]
    assert "resolution.run_started" in actions
    assert "resolution.run_finished" in actions


# --- idempotency ---------------------------------------------------------------------


def test_running_resolution_twice_changes_nothing(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    store(
        db,
        source,
        discovery_run,
        [
            payload(
                "ChIJone",
                "Lone Star Plumbing",
                phone="(512) 555-0101",
                website="https://lonestar.invalid/",
            ),
            payload(
                "ChIJtwo",
                "Lone Star Plumbing",
                phone="(512) 555-0101",
                website="https://lonestar.invalid/",
            ),
        ],
    )
    db.commit()
    resolve(db, discovery_run)

    before = _snapshot(db)
    second = resolve(db, discovery_run)

    assert _snapshot(db) == before
    assert second.result_summary is not None
    assert second.result_summary["processed"] == 0, "nothing changed, so there is nothing to redo"
    assert second.result_summary["created"] == 0


def test_a_record_already_linked_is_never_scored_again(
    db: Session, source: Source, discovery_run: JobRun
) -> None:
    """Rule 0: the same source record keeps the business it already has."""
    store(db, source, discovery_run, [payload("ChIJone", "Lone Star", phone="(512) 555-0101")])
    db.commit()
    resolve(db, discovery_run)
    record = db.scalars(select(DiscoveredRecord)).one()
    original = record.business_id

    # A decoy that would otherwise out-score nothing but still be a candidate.
    db.add(
        Business(display_name="Lone Star", normalized_name="lone star", phone_e164="+15125550101")
    )
    db.commit()

    resolve(db, discovery_run)
    db.expire_all()

    assert db.scalars(select(DiscoveredRecord)).one().business_id == original


def _snapshot(session: Session) -> dict[str, object]:
    return {
        "businesses": sorted(
            (str(b.id), b.display_name, b.phone_e164) for b in session.scalars(select(Business))
        ),
        "links": sorted(
            (r.source_record_id, str(r.business_id), r.resolution_status.value)
            for r in session.scalars(select(DiscoveredRecord))
        ),
        "values": sorted(
            (str(v.business_id), v.field, v.value)
            for v in session.scalars(select(BusinessFieldValue))
        ),
    }


def test_an_unknown_run_id_is_not_found(db: Session) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        resolution.enqueue_resolution(db, uuid.uuid4())
