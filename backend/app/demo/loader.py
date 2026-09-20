"""`make load-demo-data`: store the demo fixture as a discovery run and resolve it.

Everything it writes goes through the same code paths as a real discovery run, so what
resolution then sees is indistinguishable from a real one. It is idempotent: the search
job, the run and every record are keyed, so loading twice changes nothing.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import ValidationFailedError
from app.core.logging import get_logger, log_fields
from app.modules.adapters import registry
from app.modules.adapters.base import RawDoc
from app.modules.adapters.demo_fixture import SOURCE_NAME, demo_places, load_fixture
from app.modules.auth.models import Role, User
from app.modules.compliance.models import Suppression
from app.modules.discovery import service as discovery
from app.modules.discovery.schemas import DiscoveryResultSummary
from app.modules.jobs.models import JobRun, JobRunStatus, SearchJob, SearchJobStatus
from app.modules.jobs.service import DISCOVERY_JOB_KIND, enqueue_run
from app.modules.opportunities.models import Opportunity
from app.modules.opportunities.service import enqueue_classification_for_run
from app.modules.resolution.service import enqueue_resolution
from app.modules.review.models import ReviewDecision
from app.modules.sources.models import Source

logger = get_logger("app.demo")

SEARCH_JOB_NAME = "Demo - Austin plumbers"
DISCOVERY_IDEMPOTENCY_KEY = "demo:austin-plumbers:discovery"


@dataclass(frozen=True)
class DemoLoadResult:
    search_job_id: uuid.UUID
    discovery_run_id: uuid.UUID
    resolution_run_id: uuid.UUID
    stored_new: int
    updated: int


@dataclass(frozen=True)
class DemoPipelineResult:
    """What running the demo through to the end did."""

    resolution_status: JobRunStatus
    resolution_summary: dict[str, Any]
    audit_run_id: uuid.UUID | None
    audit_status: JobRunStatus | None
    audit_summary: dict[str, Any]
    classification_run_id: uuid.UUID | None = None
    classification_status: JobRunStatus | None = None
    classification_summary: dict[str, Any] = field(default_factory=dict)


def load_demo_data(session: Session, *, now: datetime | None = None) -> DemoLoadResult:
    """Store every fixture record and queue the resolution run that dedupes them."""
    if not get_settings().is_development:
        raise ValidationFailedError(
            "The demo fixture is only available in development",
            details={"environment": get_settings().environment},
        )

    moment = now or datetime.now(UTC)
    source = _demo_source(session)
    job = _search_job(session, actor=_actor(session))

    run = enqueue_run(
        session,
        search_job_id=job.id,
        kind=DISCOVERY_JOB_KIND,
        idempotency_key=DISCOVERY_IDEMPOTENCY_KEY,
        progress_total=len(demo_places()),
    )

    summary = DiscoveryResultSummary()
    adapter = registry.get(SOURCE_NAME)
    ttl_days = int(adapter.get_source_metadata().content_ttl_days)

    for rank, payload in enumerate(demo_places()):
        raw = RawDoc(
            source=SOURCE_NAME,
            source_record_id=str(payload["id"]),
            source_url=None,
            payload=payload,
            fetched_at=moment,
        )
        summary.fetched += 1
        if not adapter.validate(raw).valid:  # pragma: no cover - the fixture is checked in
            summary.invalid += 1
            continue
        record, created = discovery.store_raw(
            session, source_id=source.id, raw=raw, content_ttl_days=ttl_days, now=moment
        )
        discovery.add_sighting(
            session, record=record, job_run_id=run.id, search_job_id=job.id, rank=rank, now=moment
        )
        summary.stored_new += 1 if created else 0
        summary.updated += 0 if created else 1

    # The records are already here, so the run is finished the moment it is written.
    run.status = JobRunStatus.done
    run.progress_done = summary.fetched
    run.started_at = run.started_at or moment
    run.finished_at = moment
    run.result_summary = summary.model_dump()
    session.flush()

    resolution = enqueue_resolution(session, run.id, idempotency_key=f"resolution:{run.id}")
    session.commit()

    logger.info(
        "demo data loaded",
        extra=log_fields(
            search_job_id=str(job.id),
            discovery_run_id=str(run.id),
            resolution_run_id=str(resolution.id),
            **summary.model_dump(),
        ),
    )
    return DemoLoadResult(
        search_job_id=job.id,
        discovery_run_id=run.id,
        resolution_run_id=resolution.id,
        stored_new=summary.stored_new,
        updated=summary.updated,
    )


def run_pipeline(result: DemoLoadResult) -> DemoPipelineResult:
    """Run the queued resolution, the audit run it triggers, then the classification run.

    `make load-demo-data` is meant to leave a developer with a finished dataset — 30
    businesses, their website audits *and* their scored opportunities — rather than run
    ids to poll. The audits are answered from the checked-in demo sites and the AI step by
    the fake provider, so this makes no network call and needs no API key.
    """
    from app.workers.tasks import execute_job_run

    resolution_status = execute_job_run(result.resolution_run_id)
    audit_run_id: uuid.UUID | None = None
    audit_status: JobRunStatus | None = None
    audit_summary: dict[str, Any] = {}
    resolution_summary: dict[str, Any] = {}

    with session_scope() as session:
        resolution = session.get(JobRun, result.resolution_run_id)
        resolution_summary = dict((resolution.result_summary if resolution else None) or {})
        audit_run = session.scalars(
            select(JobRun).where(JobRun.idempotency_key == audit_key(result.resolution_run_id))
        ).first()
        audit_run_id = audit_run.id if audit_run is not None else None

    classification_run_id: uuid.UUID | None = None
    classification_status: JobRunStatus | None = None
    classification_summary: dict[str, Any] = {}
    if audit_run_id is not None:
        audit_status = execute_job_run(audit_run_id)
        with session_scope() as session:
            audit_run = session.get(JobRun, audit_run_id)
            audit_summary = dict((audit_run.result_summary if audit_run else None) or {})
            classification_run = session.scalars(
                select(JobRun).where(JobRun.idempotency_key == classification_key(audit_run_id))
            ).first()
            classification_run_id = (
                classification_run.id if classification_run is not None else None
            )

    if classification_run_id is not None:
        classification_status = execute_job_run(classification_run_id)
        with session_scope() as session:
            classification_run = session.get(JobRun, classification_run_id)
            classification_summary = dict(
                (classification_run.result_summary if classification_run else None) or {}
            )

    return DemoPipelineResult(
        resolution_status=resolution_status,
        resolution_summary=resolution_summary,
        audit_run_id=audit_run_id,
        audit_status=audit_status,
        audit_summary=audit_summary,
        classification_run_id=classification_run_id,
        classification_status=classification_status,
        classification_summary=classification_summary,
    )


@dataclass(frozen=True)
class DemoResetResult:
    """What `reset-demo-data` removed and what it scored again."""

    decisions_deleted: int
    suppressions_deleted: int
    opportunities_deleted: int
    classification_run_id: uuid.UUID | None
    classification_status: JobRunStatus | None
    classification_summary: dict[str, Any] = field(default_factory=dict)


def reset_demo_data(session: Session) -> DemoResetResult:
    """Put the demo back to "freshly loaded": no decisions, no suppressions, every opportunity
    pending again. Development only; `make e2e` runs it so the smoke never depends on the
    state a human left behind.

    It removes every review decision, every suppression and every opportunity, then runs a
    fresh classification of the demo audit run. Businesses, audits and AI classifications
    stay (the fake provider answers from checked-in files, and the audit needs no network),
    so the result is byte-for-byte what `make load-demo-data` produced. Duplicate merges
    are not undone: a merge rewrites businesses and is not a review decision.
    """
    if not get_settings().is_development:
        raise ValidationFailedError(
            "The demo data can only be reset in development",
            details={"environment": get_settings().environment},
        )
    audit_run = _demo_audit_run(session)
    if audit_run is None:
        raise ValidationFailedError(
            "No finished demo audit run to classify again; run `make load-demo-data` first"
        )

    decisions = _wipe(session, ReviewDecision)
    suppressions = _wipe(session, Suppression)
    opportunities = _wipe(session, Opportunity)
    run = enqueue_classification_for_run(
        session,
        audit_run.id,
        idempotency_key=f"classification:{audit_run.id}:reset:{uuid.uuid4()}",
    )
    run_id = run.id
    session.commit()
    logger.info(
        "demo review state reset",
        extra=log_fields(
            decisions_deleted=decisions,
            suppressions_deleted=suppressions,
            opportunities_deleted=opportunities,
            classification_run_id=str(run_id),
        ),
    )

    from app.workers.tasks import execute_job_run

    status = execute_job_run(run_id)
    with session_scope() as fresh:
        finished = fresh.get(JobRun, run_id)
        summary = dict((finished.result_summary if finished else None) or {})
    return DemoResetResult(
        decisions_deleted=decisions,
        suppressions_deleted=suppressions,
        opportunities_deleted=opportunities,
        classification_run_id=run_id,
        classification_status=status,
        classification_summary=summary,
    )


def _wipe(session: Session, model: type[Any]) -> int:
    count = int(session.scalar(select(func.count()).select_from(model)) or 0)
    session.execute(delete(model))
    return count


def _demo_audit_run(session: Session) -> JobRun | None:
    """The audit run `load-demo-data` produced, found by the keys the loader gives the runs."""
    discovery = session.scalars(
        select(JobRun).where(JobRun.idempotency_key == DISCOVERY_IDEMPOTENCY_KEY)
    ).first()
    if discovery is None:
        return None
    resolution = session.scalars(
        select(JobRun).where(JobRun.idempotency_key == f"resolution:{discovery.id}")
    ).first()
    if resolution is None:
        return None
    audit = session.scalars(
        select(JobRun).where(JobRun.idempotency_key == audit_key(resolution.id))
    ).first()
    if audit is None or audit.status is not JobRunStatus.done:
        return None
    return audit


def audit_key(resolution_run_id: uuid.UUID) -> str:
    """The idempotency key `follow_up` gives the audit run of one resolution run."""
    return f"audit:{resolution_run_id}"


def classification_key(audit_run_id: uuid.UUID) -> str:
    """The idempotency key `follow_up` gives the classification run of one audit run."""
    return f"classification:{audit_run_id}"


def _demo_source(session: Session) -> Source:
    """The `demo_fixture` row, created by the same `sync_sources` every source uses."""
    registry.sync_sources(session)
    session.flush()
    source = session.scalars(select(Source).where(Source.name == SOURCE_NAME)).first()
    if source is None:  # pragma: no cover - guarded by is_development above
        raise ValidationFailedError("The demo source is not registered in this environment")
    return source


def _actor(session: Session) -> User:
    """A search job needs an owner; the oldest admin is the one that exists everywhere."""
    admin = session.scalars(
        select(User).where(User.role == Role.admin).order_by(User.created_at.asc()).limit(1)
    ).first()
    if admin is None:
        raise ValidationFailedError(
            "There is no admin to own the demo search job. Run `make seed-admin` first."
        )
    return admin


def _search_job(session: Session, *, actor: User) -> SearchJob:
    existing = session.scalars(select(SearchJob).where(SearchJob.name == SEARCH_JOB_NAME)).first()
    if existing is not None:
        return existing

    spec = load_fixture().get("search_job", {})
    job = SearchJob(
        name=SEARCH_JOB_NAME,
        geo=spec.get("geo", {"city": "Austin", "state": "TX"}),
        industry=spec.get("industry", "plumber"),
        source_ids=[_demo_source(session).id],
        status=SearchJobStatus.active,
        created_by=actor.id,
    )
    session.add(job)
    session.flush()
    return job


def find_run(session: Session, run_id: uuid.UUID) -> JobRun | None:  # pragma: no cover - helper
    return session.get(JobRun, run_id)
