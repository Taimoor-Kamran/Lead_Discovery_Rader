"""Retention: stored provider content expires, place IDs do not.

Google Maps Platform terms cap how long Places content may be kept but allow place IDs to
be cached indefinitely. `purge-expired` is what makes that difference real in the data.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.adapters import registry
from app.modules.adapters.base import RawDoc
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.auth.models import User
from app.modules.discovery.models import DiscoveredRecord
from app.modules.discovery.service import add_sighting, payload_hash, purge_expired, store_raw
from app.modules.jobs.models import JobRun, JobRunStatus, SearchJob, SearchJobStatus
from app.modules.sources.models import Source

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def places_source(db: Session) -> Source:
    sources = registry.sync_sources(db)
    db.commit()
    return next(s for s in sources if s.name == GooglePlacesAdapter.name)


def raw(place_id: str, **extra: object) -> RawDoc:
    return RawDoc(
        source=GooglePlacesAdapter.name,
        source_record_id=place_id,
        source_url=f"https://www.google.com/maps/place/?q=place_id:{place_id}",
        payload={"id": place_id, "displayName": {"text": "Some Plumber"}, **extra},
        fetched_at=NOW,
    )


def test_a_stored_record_expires_the_configured_number_of_days_later(db: Session) -> None:
    source = places_source(db)

    record, created = store_raw(
        db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=30, now=NOW
    )
    db.commit()

    assert created is True
    assert record.content_expires_at == NOW + timedelta(days=30)
    assert record.purged_at is None


def test_a_ttl_of_zero_means_no_expiry_is_recorded(db: Session) -> None:
    source = places_source(db)

    record, _ = store_raw(db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=0, now=NOW)
    db.commit()

    assert record.content_expires_at is None


def test_re_storing_a_record_moves_its_expiry_forward(db: Session) -> None:
    source = places_source(db)
    store_raw(db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=30, now=NOW)
    db.commit()

    later = NOW + timedelta(days=10)
    record, created = store_raw(
        db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=30, now=later
    )
    db.commit()

    assert created is False
    assert record.first_discovered_at == NOW
    assert record.last_discovered_at == later
    assert record.content_expires_at == later + timedelta(days=30)


def test_the_payload_hash_changes_only_when_the_payload_does(db: Session) -> None:
    source = places_source(db)
    record, _ = store_raw(db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=30, now=NOW)
    db.commit()
    unchanged = record.payload_hash

    store_raw(db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=30, now=NOW)
    db.commit()
    assert record.payload_hash == unchanged

    record, _ = store_raw(
        db,
        source_id=source.id,
        raw=raw("ChIJone", websiteUri="https://new-site.example.test/"),
        content_ttl_days=30,
        now=NOW,
    )
    db.commit()
    assert record.payload_hash != unchanged


def test_the_hash_ignores_key_order(db: Session) -> None:
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})


def test_purge_drops_expired_content_and_keeps_the_place_id(db: Session) -> None:
    source = places_source(db)
    expired, _ = store_raw(
        db,
        source_id=source.id,
        raw=raw("ChIJold"),
        content_ttl_days=30,
        now=NOW - timedelta(days=40),
    )
    fresh, _ = store_raw(db, source_id=source.id, raw=raw("ChIJnew"), content_ttl_days=30, now=NOW)
    db.commit()

    purged = purge_expired(db, now=NOW)
    db.commit()
    db.expire_all()

    assert purged == 1
    expired = db.get(DiscoveredRecord, expired.id)  # type: ignore[assignment]
    fresh = db.get(DiscoveredRecord, fresh.id)  # type: ignore[assignment]
    assert expired.raw_payload is None
    assert expired.purged_at == NOW
    assert expired.source_record_id == "ChIJold", "the place ID may be kept indefinitely"
    assert expired.source_url and expired.first_discovered_at
    assert fresh.raw_payload is not None, "an unexpired record is untouched"
    assert fresh.purged_at is None


def test_purging_twice_does_not_report_the_same_record_again(db: Session) -> None:
    source = places_source(db)
    store_raw(
        db,
        source_id=source.id,
        raw=raw("ChIJold"),
        content_ttl_days=30,
        now=NOW - timedelta(days=40),
    )
    db.commit()

    assert purge_expired(db, now=NOW) == 1
    db.commit()

    assert purge_expired(db, now=NOW) == 0


def test_a_record_with_no_expiry_is_never_purged(db: Session) -> None:
    source = places_source(db)
    store_raw(db, source_id=source.id, raw=raw("ChIJforever"), content_ttl_days=0, now=NOW)
    db.commit()

    assert purge_expired(db, now=NOW + timedelta(days=3650)) == 0


def test_rediscovering_a_purged_record_brings_its_content_back(db: Session) -> None:
    source = places_source(db)
    store_raw(
        db,
        source_id=source.id,
        raw=raw("ChIJold"),
        content_ttl_days=30,
        now=NOW - timedelta(days=40),
    )
    db.commit()
    purge_expired(db, now=NOW)
    db.commit()

    record, created = store_raw(
        db, source_id=source.id, raw=raw("ChIJold"), content_ttl_days=30, now=NOW
    )
    db.commit()

    assert created is False, "a purge never loses the identity of a record"
    assert record.raw_payload is not None
    assert record.purged_at is None


def test_a_purged_record_reads_back_with_null_fields_rather_than_stale_ones(db: Session) -> None:
    from app.modules.discovery import service

    source = places_source(db)
    record, _ = store_raw(
        db,
        source_id=source.id,
        raw=raw("ChIJold"),
        content_ttl_days=30,
        now=NOW - timedelta(days=40),
    )
    db.commit()
    purge_expired(db, now=NOW)
    db.commit()
    db.expire_all()

    summary = service.summarize(
        db.get(DiscoveredRecord, record.id),  # type: ignore[arg-type]
        GooglePlacesAdapter.name,
    )

    assert summary.display_name is None
    assert summary.source_record_id == "ChIJold"
    assert summary.purged_at is not None


def test_one_sighting_per_record_and_run(db: Session, sales_user: User) -> None:
    source = places_source(db)
    job = SearchJob(
        name="j",
        geo={"city": "Austin", "state": "TX"},
        industry="plumber",
        source_ids=[source.id],
        status=SearchJobStatus.active,
        created_by=sales_user.id,
    )
    db.add(job)
    run = JobRun(search_job_id=job.id, kind="discovery", status=JobRunStatus.queued)
    db.add(run)
    db.flush()
    record, _ = store_raw(db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=30, now=NOW)

    add_sighting(db, record=record, job_run_id=run.id, search_job_id=job.id, rank=3, now=NOW)
    again = add_sighting(
        db, record=record, job_run_id=run.id, search_job_id=job.id, rank=7, now=NOW
    )
    db.commit()

    from app.modules.discovery.models import RecordSighting

    rows = list(db.scalars(select(RecordSighting)))
    assert len(rows) == 1
    assert again.rank == 7, "the latest position wins, no duplicate row"


def test_two_sources_may_share_a_source_record_id(db: Session) -> None:
    places = places_source(db)
    other = Source(name="other", kind="api", config={}, enabled=True)
    db.add(other)
    db.flush()

    store_raw(db, source_id=places.id, raw=raw("shared-id"), content_ttl_days=30, now=NOW)
    store_raw(db, source_id=other.id, raw=raw("shared-id"), content_ttl_days=30, now=NOW)
    db.commit()

    assert len(list(db.scalars(select(DiscoveredRecord)))) == 2


def test_a_second_row_for_the_same_place_is_impossible(db: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    source = places_source(db)
    store_raw(db, source_id=source.id, raw=raw("ChIJone"), content_ttl_days=30, now=NOW)
    db.commit()

    db.add(
        DiscoveredRecord(
            id=uuid.uuid4(),
            source_id=source.id,
            source_record_id="ChIJone",
            first_discovered_at=NOW,
            last_discovered_at=NOW,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    else:  # pragma: no cover - the unique constraint is missing
        raise AssertionError("the (source_id, source_record_id) unique key is not enforced")
