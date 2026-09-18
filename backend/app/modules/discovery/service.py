"""Storing what discovery found, reading it back, metering calls and purging content."""

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.core.errors import NotFoundError
from app.core.http import ApiCallRecord
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.modules.adapters import registry
from app.modules.adapters.base import Candidate, RawDoc
from app.modules.adapters.registry import UnknownAdapterError
from app.modules.discovery.models import ApiCall, DiscoveredRecord, RecordSighting
from app.modules.discovery.schemas import (
    DiscoveredRecordDetail,
    DiscoveredRecordSummary,
    RecordSightingRead,
)
from app.modules.sources.models import Source

logger = get_logger("app.discovery")


def payload_hash(payload: dict[str, Any]) -> str:
    """A stable hash of a payload, so an unchanged re-discovery is visible as unchanged."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def store_raw(
    session: Session,
    *,
    source_id: uuid.UUID,
    raw: RawDoc,
    content_ttl_days: int,
    now: datetime | None = None,
) -> tuple[DiscoveredRecord, bool]:
    """Insert or refresh one record. Returns the row and whether it was newly created.

    Re-discovering a place never creates a second row: the unique key is
    `(source_id, source_record_id)`, and a repeat updates the payload and the timestamps.
    """
    seen_at = now or datetime.now(UTC)
    expires_at = seen_at + timedelta(days=content_ttl_days) if content_ttl_days > 0 else None

    record = session.scalars(
        select(DiscoveredRecord).where(
            DiscoveredRecord.source_id == source_id,
            DiscoveredRecord.source_record_id == raw.source_record_id,
        )
    ).first()

    if record is None:
        record = DiscoveredRecord(
            source_id=source_id,
            source_record_id=raw.source_record_id,
            source_url=raw.source_url,
            raw_payload=raw.payload,
            payload_hash=payload_hash(raw.payload),
            first_discovered_at=seen_at,
            last_discovered_at=seen_at,
            content_expires_at=expires_at,
        )
        session.add(record)
        session.flush()
        return record, True

    record.source_url = raw.source_url
    record.raw_payload = raw.payload
    record.payload_hash = payload_hash(raw.payload)
    record.last_discovered_at = seen_at
    record.content_expires_at = expires_at
    record.purged_at = None
    session.flush()
    return record, False


def add_sighting(
    session: Session,
    *,
    record: DiscoveredRecord,
    job_run_id: uuid.UUID,
    search_job_id: uuid.UUID | None,
    rank: int,
    now: datetime | None = None,
) -> RecordSighting:
    """Note that this run saw this record. One sighting per (record, run)."""
    existing = session.scalars(
        select(RecordSighting).where(
            RecordSighting.discovered_record_id == record.id,
            RecordSighting.job_run_id == job_run_id,
        )
    ).first()
    if existing is not None:
        existing.rank = rank
        return existing

    sighting = RecordSighting(
        discovered_record_id=record.id,
        job_run_id=job_run_id,
        search_job_id=search_job_id,
        seen_at=now or datetime.now(UTC),
        rank=rank,
    )
    session.add(sighting)
    session.flush()
    return sighting


def purge_expired(session: Session, *, now: datetime | None = None) -> int:
    """Drop stored provider content whose retention window has closed.

    The row, its place ID and its provenance stay; only `raw_payload` goes. That is what
    the Google Maps Platform terms allow us to keep indefinitely.
    """
    moment = now or datetime.now(UTC)
    result = session.execute(
        update(DiscoveredRecord)
        .where(
            DiscoveredRecord.content_expires_at.is_not(None),
            DiscoveredRecord.content_expires_at < moment,
            DiscoveredRecord.raw_payload.is_not(None),
        )
        .values(raw_payload=None, purged_at=moment)
    )
    purged = int(getattr(result, "rowcount", 0) or 0)
    if purged:
        logger.info("purged expired record content", extra={"records": purged})
    return purged


# --- metering ---------------------------------------------------------------------


def record_api_call(
    session: Session, *, source_id: uuid.UUID, job_run_id: uuid.UUID | None, call: ApiCallRecord
) -> ApiCall:
    row = ApiCall(
        source_id=source_id,
        job_run_id=job_run_id,
        endpoint=call.endpoint,
        status_code=call.status_code,
        attempt=call.attempt,
        duration_ms=call.duration_ms,
        error_class=call.error_class,
    )
    session.add(row)
    session.flush()
    return row


def api_call_meter(job_run_id: uuid.UUID | None) -> Callable[[ApiCallRecord], None]:
    """A metering hook that commits each call in its own transaction.

    Separate on purpose: when a run fails and rolls back, what it spent must still be on
    record.
    """

    def meter(call: ApiCallRecord) -> None:
        with session_scope() as session:
            source_id = session.scalar(select(Source.id).where(Source.name == call.source))
            if source_id is None:
                logger.warning(
                    "skipping api call metering for an unregistered source",
                    extra={"source": call.source},
                )
                return
            record_api_call(session, source_id=source_id, job_run_id=job_run_id, call=call)

    return meter


def count_api_calls(session: Session, job_run_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(ApiCall).where(ApiCall.job_run_id == job_run_id)
        )
        or 0
    )


# --- reads ------------------------------------------------------------------------


def get_record(session: Session, record_id: uuid.UUID) -> DiscoveredRecord:
    record = session.get(DiscoveredRecord, record_id)
    if record is None:
        raise NotFoundError(
            "Discovered record not found", details={"discovered_record_id": str(record_id)}
        )
    return record


def list_records_for_run(
    session: Session,
    job_run_id: uuid.UUID,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[DiscoveredRecordSummary]:
    """Every record this run saw, newest first."""
    stmt: Select[tuple[DiscoveredRecord]] = (
        select(DiscoveredRecord)
        .join(RecordSighting, RecordSighting.discovered_record_id == DiscoveredRecord.id)
        .where(RecordSighting.job_run_id == job_run_id)
        .order_by(DiscoveredRecord.created_at.desc(), DiscoveredRecord.id.desc())
        .limit(limit + 1)
    )
    stmt = apply_cursor(stmt, DiscoveredRecord.created_at, DiscoveredRecord.id, cursor)
    rows = list(session.scalars(stmt))

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)

    names = _source_names(session, [row.source_id for row in rows])
    return Page[DiscoveredRecordSummary](
        items=[summarize(row, names.get(row.source_id, "")) for row in rows],
        next_cursor=next_cursor,
    )


def summarize(record: DiscoveredRecord, source_name: str) -> DiscoveredRecordSummary:
    candidate = _candidate(record, source_name)
    return DiscoveredRecordSummary(
        id=record.id,
        source_id=record.source_id,
        source=source_name,
        source_record_id=record.source_record_id,
        source_url=record.source_url,
        display_name=candidate.display_name,
        formatted_address=candidate.formatted_address,
        phone=candidate.phone,
        website=candidate.website,
        business_status=candidate.business_status,
        first_discovered_at=record.first_discovered_at,
        last_discovered_at=record.last_discovered_at,
        purged_at=record.purged_at,
        created_at=record.created_at,
    )


def detail(session: Session, record: DiscoveredRecord) -> DiscoveredRecordDetail:
    source_name = _source_names(session, [record.source_id]).get(record.source_id, "")
    candidate = _candidate(record, source_name)
    sightings = session.scalars(
        select(RecordSighting)
        .where(RecordSighting.discovered_record_id == record.id)
        .order_by(RecordSighting.seen_at.desc(), RecordSighting.id.desc())
    )
    return DiscoveredRecordDetail(
        **summarize(record, source_name).model_dump(),
        raw_payload=record.raw_payload,
        payload_hash=record.payload_hash,
        business_id=record.business_id,
        published_at=record.published_at,
        extracted_at=record.extracted_at,
        evidence_text=record.evidence_text,
        confidence=record.confidence,
        content_expires_at=record.content_expires_at,
        types=candidate.types,
        lat=candidate.lat,
        lng=candidate.lng,
        sightings=[RecordSightingRead.model_validate(s) for s in sightings],
    )


def _candidate(record: DiscoveredRecord, source_name: str) -> Candidate:
    """Map a stored payload through its own adapter. A purged record maps to all-null."""
    if not record.raw_payload or not source_name:
        return Candidate()
    try:
        adapter = registry.get(source_name)
    except UnknownAdapterError:
        return Candidate()
    raw = RawDoc(
        source=source_name,
        source_record_id=record.source_record_id,
        source_url=record.source_url,
        payload=record.raw_payload,
        fetched_at=record.last_discovered_at,
    )
    try:
        return adapter.normalize(raw)
    except Exception:
        logger.warning(
            "stored payload no longer maps cleanly", extra={"discovered_record_id": str(record.id)}
        )
        return Candidate()


def _source_names(session: Session, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not source_ids:
        return {}
    rows = session.execute(
        select(Source.id, Source.name).where(Source.id.in_(set(source_ids)))
    ).all()
    return {row.id: row.name for row in rows}
