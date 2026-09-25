"""The `audit` job: what it picks up, how it isolates a failure, and how it is cancelled."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import SOURCE_NAME as PLACES_SOURCE
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.audit_web.worker import run_audits
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord, RecordSighting
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import AUDIT_JOB_KIND, DISCOVERY_JOB_KIND, RESOLUTION_JOB_KIND
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.sources.models import Source
from app.workers import tasks
from tests.integration.test_website_audits_api import make_business, tools

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def source(session: Session) -> Source:
    """A real source row for the records below. The demo source is development-only."""
    registry.sync_sources(session)
    session.flush()
    return session.scalars(select(Source).where(Source.name == PLACES_SOURCE)).one()


def pipeline(session: Session, businesses: list[Business]) -> tuple[JobRun, JobRun]:
    """A finished discovery run and the resolution run that linked these businesses."""
    discovery = JobRun(kind=DISCOVERY_JOB_KIND, status=JobRunStatus.done)
    session.add(discovery)
    session.flush()
    resolution = JobRun(
        kind=RESOLUTION_JOB_KIND,
        status=JobRunStatus.done,
        params={"parent_run_id": str(discovery.id)},
    )
    session.add(resolution)
    session.flush()

    src = source(session)
    for index, business in enumerate(businesses):
        record = DiscoveredRecord(
            source_id=src.id,
            source_record_id=f"audit-run-{index}-{uuid.uuid4().hex[:6]}",
            raw_payload={"id": "x"},
            payload_hash="hash",
            first_discovered_at=NOW,
            last_discovered_at=NOW,
            business_id=business.id,
        )
        session.add(record)
        session.flush()
        session.add(
            RecordSighting(
                discovered_record_id=record.id,
                job_run_id=discovery.id,
                search_job_id=None,
                seen_at=NOW,
                rank=index,
            )
        )
    session.flush()
    return discovery, resolution


def audit_run(session: Session, resolution: JobRun) -> JobRun:
    run = JobRun(
        kind=AUDIT_JOB_KIND,
        status=JobRunStatus.running,
        params={"parent_run_id": str(resolution.id)},
    )
    session.add(run)
    session.flush()
    session.commit()
    return run


def run_with(
    session: Session, run: JobRun, audit_tools: Any, monkeypatch: pytest.MonkeyPatch
) -> JobRun:
    """Drive the handler with an injected fetcher instead of the production one."""
    monkeypatch.setattr(service.AuditTools, "build", classmethod(lambda cls, **kwargs: audit_tools))
    run_audits(session, run)
    session.commit()
    return run


# --- what a run picks up ------------------------------------------------------------------


def test_a_run_audits_the_businesses_its_resolution_run_touched(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_business(db, name="First Plumbing")
    second = make_business(db, name="Second Plumbing")
    untouched = make_business(db, name="Not In This Run")
    _, resolution = pipeline(db, [first, second])
    run = audit_run(db, resolution)

    run_with(db, run, tools(), monkeypatch)

    assert run.result_summary == {
        "audited": 2,
        "skipped": 0,
        "robots_blocked": 0,
        "unreachable": 0,
        "failed": 0,
        "psi_calls": 2,
    }
    assert run.progress_total == 2
    assert run.progress_done == 2
    assert service.latest_audit(db, untouched.id) is None


def test_a_permanently_closed_business_is_left_alone(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_business = make_business(db, name="Still Trading")
    closed = make_business(db, name="Gone", status=BusinessStatus.closed_permanently)
    _, resolution = pipeline(db, [open_business, closed])
    run = audit_run(db, resolution)

    run_with(db, run, tools(), monkeypatch)

    assert service.latest_audit(db, closed.id) is None
    assert service.latest_audit(db, open_business.id) is not None


@pytest.mark.parametrize(
    ("kind", "website", "code"),
    [
        (WebsiteKind.none, None, "no_website"),
        (WebsiteKind.social_profile, "https://www.facebook.com/someplumber", "social_profile_only"),
    ],
)
def test_a_business_without_a_site_of_its_own_is_skipped_without_a_fetch(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    kind: WebsiteKind,
    website: str | None,
    code: str,
) -> None:
    business = make_business(db, name="No Site Of Its Own", website=website, kind=kind)
    _, resolution = pipeline(db, [business])
    run = audit_run(db, resolution)
    audit_tools = tools()

    run_with(db, run, audit_tools, monkeypatch)

    audit = service.latest_audit(db, business.id)
    assert audit is not None
    assert audit.status is AuditStatus.skipped
    assert audit.finding_codes == [code]
    assert audit_tools.fetcher.backends[0].calls == [], "no request at all, not even robots.txt"
    assert run.result_summary is not None
    assert run.result_summary["skipped"] == 1


def test_a_robots_disallowed_site_is_never_asked_for_its_homepage(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promise the whole robots policy rests on: zero page requests."""
    business = make_business(db, name="Asked Us To Stay Away")
    _, resolution = pipeline(db, [business])
    run = audit_run(db, resolution)
    audit_tools = tools(robots="User-agent: *\nDisallow: /\n")

    run_with(db, run, audit_tools, monkeypatch)

    audit = service.latest_audit(db, business.id)
    assert audit is not None
    assert audit.status is AuditStatus.robots_blocked
    assert audit.finding_codes == ["robots_blocked"]
    # robots.txt was read; the homepage never was.
    assert audit_tools.fetcher.backends[0].calls == ["https://wellington.invalid/robots.txt"]
    assert audit_tools.fetcher.requested == []
    assert audit.checks == {}
    assert audit.page_text is None


def test_a_site_that_answers_500_is_recorded_as_unreachable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    business = make_business(db, name="Server Error Plumbing")
    _, resolution = pipeline(db, [business])
    run = audit_run(db, resolution)
    audit_tools = tools()

    def failing(request: Any) -> Any:
        from app.core.fetch_backends import BackendResponse

        if request.parts.path == "/robots.txt":
            return BackendResponse(
                status_code=404, headers={"content-type": "text/plain"}, body=b""
            )
        return BackendResponse(status_code=503, headers={"content-type": "text/html"}, body=b"")

    monkeypatch.setattr(audit_tools.fetcher.backends[0], "get", failing)
    run_with(db, run, audit_tools, monkeypatch)

    audit = service.latest_audit(db, business.id)
    assert audit is not None
    assert audit.status is AuditStatus.unreachable
    assert audit.http_status == 503
    assert audit.finding_codes == ["unreachable"]


def test_a_business_audited_recently_is_not_audited_again(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    business = make_business(db)
    _, resolution = pipeline(db, [business])
    first = audit_run(db, resolution)
    run_with(db, first, tools(), monkeypatch)

    second = audit_run(db, resolution)
    run_with(db, second, tools(), monkeypatch)

    assert second.progress_total == 0
    assert len(list(db.scalars(select(WebsiteAudit)))) == 1


def test_a_business_whose_audit_is_older_than_the_max_age_is_audited_again(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    business = make_business(db)
    _, resolution = pipeline(db, [business])
    first = audit_run(db, resolution)
    run_with(db, first, tools(), monkeypatch)

    stale = service.latest_audit(db, business.id)
    assert stale is not None
    stale.created_at = datetime.now(UTC) - timedelta(days=90)
    db.commit()

    second = audit_run(db, resolution)
    run_with(db, second, tools(), monkeypatch)

    assert second.progress_total == 1
    assert len(list(db.scalars(select(WebsiteAudit)))) == 2


def test_an_audit_run_that_names_no_resolution_run_fails_loudly(db: Session) -> None:
    run = JobRun(kind=AUDIT_JOB_KIND, status=JobRunStatus.running, params={})
    db.add(run)
    db.commit()

    with pytest.raises(ValueError, match="must name the resolution run"):
        run_audits(db, run)


# --- failure isolation --------------------------------------------------------------------


def test_one_business_crashing_does_not_fail_the_run(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy = make_business(db, name="Healthy Plumbing")
    broken = make_business(db, name="Explodes On Audit")
    _, resolution = pipeline(db, [healthy, broken])
    run = audit_run(db, resolution)

    real = service.audit_business

    def sometimes_explodes(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        if business.display_name == "Explodes On Audit":
            raise RuntimeError("the parser fell over")
        return real(session, business, **kwargs)

    monkeypatch.setattr(service, "audit_business", sometimes_explodes)
    run_with(db, run, tools(), monkeypatch)

    assert run.result_summary is not None
    assert run.result_summary["audited"] == 1
    assert run.result_summary["failed"] == 1

    failed = service.latest_audit(db, broken.id)
    assert failed is not None
    assert failed.status is AuditStatus.failed
    assert "RuntimeError: the parser fell over" in failed.checks["error"]["value"]
    assert failed.findings == [], "a failure of ours is not a finding about their website"

    assert service.latest_audit(db, healthy.id) is not None, "the other business is unaffected"


def test_a_failure_leaves_the_session_usable_for_the_rest_of_the_run(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The savepoint is what makes this true; without it the whole run would roll back."""
    broken = make_business(db, name="Explodes On Audit")
    later = make_business(db, name="Audited After The Failure")
    _, resolution = pipeline(db, [broken, later])
    run = audit_run(db, resolution)

    real = service.audit_business

    def sometimes_explodes(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        if business.display_name == "Explodes On Audit":
            session.add(Business(display_name="a half-written row"))
            session.flush()
            raise RuntimeError("boom")
        return real(session, business, **kwargs)

    monkeypatch.setattr(service, "audit_business", sometimes_explodes)
    run_with(db, run, tools(), monkeypatch)

    assert service.latest_audit(db, later.id) is not None
    assert (
        db.scalars(select(Business).where(Business.display_name == "a half-written row")).first()
        is None
    ), "the half-written row was rolled back with the savepoint"


# --- the pipeline and cancellation --------------------------------------------------------


def test_a_finished_resolution_run_queues_its_own_audit_run(db: Session) -> None:
    business = make_business(db)
    _, resolution = pipeline(db, [business])
    db.commit()

    tasks.follow_up(db, resolution)
    db.commit()

    queued = db.scalars(
        select(JobRun).where(JobRun.idempotency_key == f"audit:{resolution.id}")
    ).one()
    assert queued.kind == AUDIT_JOB_KIND
    assert queued.params == {"parent_run_id": str(resolution.id)}


def test_the_follow_up_key_stops_a_second_audit_run(db: Session) -> None:
    business = make_business(db)
    _, resolution = pipeline(db, [business])
    db.commit()

    tasks.follow_up(db, resolution)
    tasks.follow_up(db, resolution)
    db.commit()

    runs = list(
        db.scalars(select(JobRun).where(JobRun.idempotency_key == f"audit:{resolution.id}"))
    )
    assert len(runs) == 1


def test_a_cancelled_audit_run_stops_between_businesses(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation lands at a checkpoint, so one audit finishes and the next never starts."""
    first = make_business(db, name="One Of Two")
    second = make_business(db, name="Two Of Two")
    _, resolution = pipeline(db, [first, second])
    run = audit_run(db, resolution)

    real = service.audit_business
    audited: list[str] = []

    def audit_then_cancel(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        audited.append(business.display_name)
        result = real(session, business, **kwargs)
        run.cancel_requested = True
        session.flush()
        return result

    monkeypatch.setattr(service, "audit_business", audit_then_cancel)
    with pytest.raises(tasks.JobCancelledError):
        run_with(db, run, tools(), monkeypatch)

    assert len(audited) == 1, f"the run kept going after the cancel: {audited}"
    assert len(list(db.scalars(select(WebsiteAudit)))) == 1
    assert run.progress_total == 2, "it knew there were two; it only did one"


def test_a_single_business_audit_run_audits_only_that_business(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    wanted = make_business(db, name="Asked For By Hand")
    other = make_business(db, name="Not Asked For")
    db.commit()

    run = service.enqueue_audit_for_business(db, wanted.id)
    db.commit()
    run_with(db, run, tools(), monkeypatch)

    assert run.result_summary is not None
    assert run.result_summary["audited"] == 1
    assert service.latest_audit(db, wanted.id) is not None
    assert service.latest_audit(db, other.id) is None


def test_a_hand_triggered_audit_ignores_how_recently_the_business_was_audited(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    business = make_business(db)
    db.commit()
    first = service.enqueue_audit_for_business(db, business.id, idempotency_key="one")
    db.commit()
    run_with(db, first, tools(), monkeypatch)

    second = service.enqueue_audit_for_business(db, business.id, idempotency_key="two")
    db.commit()
    run_with(db, second, tools(), monkeypatch)

    assert len(list(db.scalars(select(WebsiteAudit)))) == 2


# --- the run's time limit (v0.11.0) ---------------------------------------------------------


def test_a_run_that_reaches_its_time_limit_fails_as_a_timeout_not_as_a_failed_audit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Until v0.11.0 RQ's timeout was an `Exception`: the audit loop caught it, stored the
    business it was on as a failed audit, and the run carried on and reported done."""
    from app.workers.timeouts import RunTimedOut

    others = [make_business(db, name=f"Another Business {n}") for n in range(2)]
    on_the_clock = make_business(db, name="Interrupted By The Limit")
    _, resolution = pipeline(db, [*others, on_the_clock])
    run = JobRun(
        kind=AUDIT_JOB_KIND,
        status=JobRunStatus.queued,
        params={"parent_run_id": str(resolution.id)},
    )
    db.add(run)
    db.commit()

    real = service.audit_business

    def limit_reached_midway(session: Session, business: Business, **kwargs: Any) -> WebsiteAudit:
        if business.display_name == "Interrupted By The Limit":
            raise RunTimedOut(180)
        return real(session, business, **kwargs)

    monkeypatch.setattr(service, "audit_business", limit_reached_midway)
    monkeypatch.setattr(service.AuditTools, "build", classmethod(lambda cls, **kwargs: tools()))

    with pytest.raises(RunTimedOut):
        tasks.execute_job_run(run.id, sleeper=lambda seconds: None)

    db.expire_all()
    stored = db.get(JobRun, run.id)
    assert stored is not None
    assert stored.status is JobRunStatus.failed, "not left `running` for the watchdog"
    assert stored.attempts == 1, "the same work would meet the same limit"
    assert stored.error is not None and "time limit of 180 s" in stored.error
    assert service.latest_audit(db, on_the_clock.id) is None, "no false 'failed audit'"
    stored_audits = list(db.scalars(select(WebsiteAudit).where(WebsiteAudit.job_run_id == run.id)))
    assert all(audit.status is not AuditStatus.failed for audit in stored_audits)


def test_an_audit_run_is_given_a_time_limit_for_its_businesses_not_rqs_180_seconds(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from app.core.redis import get_queue

    settings = get_settings()
    monkeypatch.setattr(settings, "audit_seconds_per_business", 1000)
    businesses = [make_business(db, name=f"Business {n}") for n in range(3)]
    _, resolution = pipeline(db, businesses)
    db.commit()

    run = service.enqueue_audits_for_run(db, resolution.id)
    job = get_queue().fetch_job(str(run.id))

    assert job is not None
    assert job.timeout == 3 * 1000, "one budget per business, not RQ's default"

    monkeypatch.setattr(settings, "audit_seconds_per_business", 1)
    small = service.enqueue_audits_for_run(db, resolution.id, idempotency_key="small")
    small_job = get_queue().fetch_job(str(small.id))
    assert small_job is not None
    assert small_job.timeout == settings.job_timeout_seconds == 1800, "never below the floor"
