"""End to end: a search job run against the recorded Places responses.

This is the spec's headline behaviour — "running a search job actually discovers
businesses" — so it is tested through the real worker, the real adapter, the real HTTP
client and a real PostgreSQL. Only the network is a recording.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.ratelimit import DailyCallCap
from app.modules.adapters import registry
from app.modules.adapters.base import Candidate, RawDoc
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.adapters.google_places.client import PLACES_BASE_URL, TEXT_SEARCH_PATH
from app.modules.auth.models import User
from app.modules.discovery.models import ApiCall, DiscoveredRecord, RecordSighting
from app.modules.jobs.models import JobRun, JobRunStatus, SearchJob, SearchJobStatus
from app.modules.jobs.service import DISCOVERY_JOB_KIND, enqueue_run
from app.modules.sources.models import Source
from app.workers import tasks
from tests.conftest import FakeClock, places_fixture

SEARCH_URL = f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}"
TOTAL_RESULTS = 60


@pytest.fixture
def places_source(db: Session, places_adapter: GooglePlacesAdapter) -> Source:
    """Register the adapters into `sources`, the way `make migrate` does."""
    sources = registry.sync_sources(db)
    db.commit()
    return next(s for s in sources if s.name == GooglePlacesAdapter.name)


@pytest.fixture
def search_job(db: Session, sales_user: User, places_source: Source) -> SearchJob:
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
    return job


def three_pages(mock_http: respx.MockRouter) -> respx.Route:
    return mock_http.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=places_fixture("text_search_page_1.json")),
            httpx.Response(200, json=places_fixture("text_search_page_2.json")),
            httpx.Response(200, json=places_fixture("text_search_page_3.json")),
        ]
    )


def start_run(db: Session, job: SearchJob, user: User) -> uuid.UUID:
    run = enqueue_run(db, search_job_id=job.id, kind=DISCOVERY_JOB_KIND, actor_id=user.id)
    db.commit()
    return run.id


def reread(db: Session, run_id: uuid.UUID) -> JobRun:
    db.expire_all()
    run = db.get(JobRun, run_id)
    assert run is not None
    return run


def count(db: Session, model: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


# --- the happy path ----------------------------------------------------------------


def test_a_run_stores_sixty_records_from_three_pages_and_reports_them(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    route = three_pages(mock_http)
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    run = reread(db, run_id)
    assert route.call_count == 3
    assert count(db, DiscoveredRecord) == TOTAL_RESULTS
    assert count(db, RecordSighting) == TOTAL_RESULTS
    assert run.result_summary == {
        "fetched": TOTAL_RESULTS,
        "stored_new": TOTAL_RESULTS,
        "updated": 0,
        "invalid": 0,
        "api_calls": 3,
    }
    assert run.progress_done == run.progress_total == TOTAL_RESULTS


def test_every_stored_record_carries_its_provenance(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    places_source: Source,
) -> None:
    # The pages answer by token, not in order, so the retry gets the same three again.
    pages = {
        None: places_fixture("text_search_page_1.json"),
        "radar-page-token-2": places_fixture("text_search_page_2.json"),
        "radar-page-token-3": places_fixture("text_search_page_3.json"),
    }
    mock_http.post(SEARCH_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=pages[json.loads(request.content).get("pageToken")]
        )
    )
    run_id = start_run(db, search_job, sales_user)
    tasks.execute_job_run(run_id)

    db.expire_all()
    records = list(db.scalars(select(DiscoveredRecord)))
    assert len(records) == TOTAL_RESULTS
    for record in records:
        assert record.source_id == places_source.id
        assert record.source_record_id.startswith("ChIJradar")
        assert record.source_url == (
            f"https://www.google.com/maps/place/?q=place_id:{record.source_record_id}"
        )
        assert record.first_discovered_at is not None
        assert record.last_discovered_at is not None
        assert record.content_expires_at is not None
        assert record.content_expires_at > record.last_discovered_at
        assert record.payload_hash
        assert record.business_id is None, "entity resolution is v0.3.0"


def test_a_record_that_only_has_a_place_id_is_stored_with_nothing_invented(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    # The pages answer by token, not in order, so the retry gets the same three again.
    pages = {
        None: places_fixture("text_search_page_1.json"),
        "radar-page-token-2": places_fixture("text_search_page_2.json"),
        "radar-page-token-3": places_fixture("text_search_page_3.json"),
    }
    mock_http.post(SEARCH_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=pages[json.loads(request.content).get("pageToken")]
        )
    )
    run_id = start_run(db, search_job, sales_user)
    tasks.execute_job_run(run_id)

    db.expire_all()
    minimal = db.scalars(
        select(DiscoveredRecord).where(
            DiscoveredRecord.source_record_id == "ChIJradar0059MinimalPlace"
        )
    ).one()
    assert minimal.raw_payload == {"id": "ChIJradar0059MinimalPlace"}

    candidate = registry.get(GooglePlacesAdapter.name).normalize(
        RawDoc(
            source=GooglePlacesAdapter.name,
            source_record_id=minimal.source_record_id,
            source_url=minimal.source_url,
            payload=minimal.raw_payload,
            fetched_at=minimal.last_discovered_at,
        )
    )
    assert candidate == Candidate(), "an absent field is null, never a guess"


def test_the_second_run_adds_sightings_but_no_new_records(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    three_pages(mock_http)
    first_run = start_run(db, search_job, sales_user)
    tasks.execute_job_run(first_run)

    db.expire_all()
    before = {r.id: r.last_discovered_at for r in db.scalars(select(DiscoveredRecord))}

    three_pages(mock_http)
    second_run = start_run(db, search_job, sales_user)
    assert tasks.execute_job_run(second_run) is JobRunStatus.done

    db.expire_all()
    after = {r.id: r.last_discovered_at for r in db.scalars(select(DiscoveredRecord))}
    assert set(after) == set(before), "no second row for a place already known"
    assert count(db, DiscoveredRecord) == TOTAL_RESULTS
    assert count(db, RecordSighting) == TOTAL_RESULTS * 2
    assert all(after[k] > before[k] for k in before), "last_discovered_at moved forward"

    run = reread(db, second_run)
    assert run.result_summary is not None
    assert run.result_summary["stored_new"] == 0
    assert run.result_summary["updated"] == TOTAL_RESULTS


def test_the_first_discovery_timestamp_is_never_rewritten(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    three_pages(mock_http)
    tasks.execute_job_run(start_run(db, search_job, sales_user))
    db.expire_all()
    first_seen = {
        r.source_record_id: r.first_discovered_at for r in db.scalars(select(DiscoveredRecord))
    }

    three_pages(mock_http)
    tasks.execute_job_run(start_run(db, search_job, sales_user))

    db.expire_all()
    still = {
        r.source_record_id: r.first_discovered_at for r in db.scalars(select(DiscoveredRecord))
    }
    assert still == first_seen


def test_a_lower_cap_stops_after_two_pages(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "places_max_results_per_job", 25)
    route = three_pages(mock_http)
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    assert route.call_count == 2
    assert count(db, DiscoveredRecord) == 25
    assert reread(db, run_id).result_summary == {
        "fetched": 25,
        "stored_new": 25,
        "updated": 0,
        "invalid": 0,
        "api_calls": 2,
    }


def test_an_empty_result_is_a_successful_run_with_nothing_stored(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_empty.json"))
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    assert count(db, DiscoveredRecord) == 0
    assert reread(db, run_id).result_summary == {
        "fetched": 0,
        "stored_new": 0,
        "updated": 0,
        "invalid": 0,
        "api_calls": 1,
    }


def test_a_place_with_no_id_is_counted_as_invalid_and_not_stored(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_with_invalid_place.json"))
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    assert count(db, DiscoveredRecord) == 2
    summary = reread(db, run_id).result_summary
    assert summary is not None
    assert (summary["fetched"], summary["stored_new"], summary["invalid"]) == (3, 2, 1)


# --- failure handling ---------------------------------------------------------------


def test_a_429_is_waited_out_and_both_attempts_are_metered(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    clock: FakeClock,
) -> None:
    mock_http.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(
                429, headers={"Retry-After": "2"}, json=places_fixture("error_429.json")
            ),
            httpx.Response(200, json=places_fixture("text_search_page_3.json")),
        ]
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    assert clock.delays == [2.0], "the Retry-After was honoured, not guessed at"
    db.expire_all()
    calls = list(db.scalars(select(ApiCall).order_by(ApiCall.id)))
    assert [(c.status_code, c.attempt) for c in calls] == [(429, 1), (200, 2)]
    assert all(c.job_run_id == run_id for c in calls)
    assert count(db, DiscoveredRecord) == 20


def test_three_500s_retry_the_run_and_then_fail_it_readably(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(500, json=places_fixture("error_500.json"))
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id, sleeper=lambda seconds: None) is JobRunStatus.failed

    run = reread(db, run_id)
    assert run.attempts == 3, "a 5xx is transient, so the v0.1.0 retry policy applies"
    assert run.error is not None
    assert "TransientError" in run.error
    assert "500" in run.error
    # Three worker attempts, each making three HTTP attempts of its own.
    assert count(db, ApiCall) == 9


def test_a_403_fails_the_run_at_once_with_the_message_an_operator_needs(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(403, json=places_fixture("error_403.json"))
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.failed

    run = reread(db, run_id)
    assert run.attempts == 1, "retrying a rejected key only wastes time"
    assert route.call_count == 1
    assert run.error is not None
    assert "AuthError" in run.error
    assert "GOOGLE_PLACES_API_KEY" in run.error
    assert "billing" in run.error


def test_a_broken_response_body_fails_the_run_without_further_retries(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("malformed_body.json"))
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.failed

    run = reread(db, run_id)
    assert run.attempts == 1
    assert run.error is not None and "SchemaError" in run.error


def test_the_daily_cap_fails_the_run_before_a_single_call_is_made(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    fake_redis: fakeredis.FakeStrictRedis,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "places_daily_call_cap", 2)
    route = mock_http.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    today = datetime.fromtimestamp(clock(), tz=UTC).date().isoformat()
    fake_redis.set(f"ratelimit:daily:google_places:{today}", 2)

    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.failed

    run = reread(db, run_id)
    assert route.call_count == 0, "the guard runs before the request, not after it"
    assert run.attempts == 1, "a cap is not a transient failure"
    assert run.error is not None and "QuotaExceededError" in run.error
    assert count(db, ApiCall) == 0


# --- cancellation ---------------------------------------------------------------------


def test_cancelling_stops_at_the_next_record_and_keeps_what_was_stored(
    client: TestClient,
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
) -> None:
    """The operator hits cancel while the run is between pages."""
    from tests.conftest import auth_headers

    headers = auth_headers(client, sales_user)
    run_id = start_run(db, search_job, sales_user)

    def cancel_then_answer(request: httpx.Request) -> httpx.Response:
        response = client.post(f"/api/v1/jobs/{run_id}/cancel", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == JobRunStatus.running.value
        return httpx.Response(200, json=places_fixture("text_search_page_2.json"))

    pages = iter(
        [
            lambda _: httpx.Response(200, json=places_fixture("text_search_page_1.json")),
            cancel_then_answer,
        ]
    )
    mock_http.post(SEARCH_URL).mock(side_effect=lambda request: next(pages)(request))

    assert tasks.execute_job_run(run_id) is JobRunStatus.cancelled

    db.expire_all()
    stored = count(db, DiscoveredRecord)
    assert stored == 20, "everything page one found is kept"
    assert count(db, RecordSighting) == stored
    run = reread(db, run_id)
    assert run.status is JobRunStatus.cancelled
    assert run.finished_at is not None
    assert run.progress_done == stored


def test_a_sighting_records_where_a_record_ranked(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    # The pages answer by token, not in order, so the retry gets the same three again.
    pages = {
        None: places_fixture("text_search_page_1.json"),
        "radar-page-token-2": places_fixture("text_search_page_2.json"),
        "radar-page-token-3": places_fixture("text_search_page_3.json"),
    }
    mock_http.post(SEARCH_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=pages[json.loads(request.content).get("pageToken")]
        )
    )
    run_id = start_run(db, search_job, sales_user)
    tasks.execute_job_run(run_id)

    db.expire_all()
    sightings = list(
        db.scalars(
            select(RecordSighting)
            .where(RecordSighting.job_run_id == run_id)
            .order_by(RecordSighting.rank)
        )
    )
    assert [s.rank for s in sightings] == list(range(TOTAL_RESULTS))
    assert all(s.search_job_id == search_job.id for s in sightings)
    assert all(s.seen_at is not None for s in sightings)


# --- disabled and missing sources ------------------------------------------------------


def test_a_disabled_source_is_skipped_rather_than_failing_the_run(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    places_source: Source,
) -> None:
    route = mock_http.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    places_source.enabled = False
    db.commit()
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    assert route.call_count == 0
    assert count(db, DiscoveredRecord) == 0


def test_a_run_with_no_sources_finishes_without_calling_anything(
    db: Session, mock_http: respx.MockRouter, sales_user: User, places_adapter: GooglePlacesAdapter
) -> None:
    job = SearchJob(
        name="no sources",
        geo={"city": "Austin", "state": "TX"},
        industry="plumber",
        source_ids=[],
        status=SearchJobStatus.active,
        created_by=sales_user.id,
    )
    db.add(job)
    db.commit()
    run_id = start_run(db, job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done
    assert count(db, DiscoveredRecord) == 0


def test_a_discovery_run_without_a_search_job_fails(
    db: Session, sales_user: User, places_adapter: GooglePlacesAdapter
) -> None:
    run = enqueue_run(db, search_job_id=None, kind=DISCOVERY_JOB_KIND, actor_id=sales_user.id)
    db.commit()

    assert tasks.execute_job_run(run.id, sleeper=lambda seconds: None) is JobRunStatus.failed


# --- the run that stopped after four places (spec v0.9.0) ------------------------------


def test_a_run_broken_partway_through_a_page_retries_and_stores_every_result(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why the real "plumber in Austin, TX" run stored four places out of twenty.

    One `places:searchText` call came back 200 with a full page, and the run stored four
    of it before `checkpoint`'s `session.refresh` hit the shared-connection bug
    (`prepared statement "_pg3_1" already exists`). The 16 results already in hand were
    never processed, the exception escaped `execute_job_run`, and the run sat in
    `running` for half an hour until the watchdog failed it — so nothing retried it
    either. Now the break fails the attempt properly, the attempt is retried, and
    `store_raw` being an upsert means the retry ends with the whole page stored.
    """
    # The pages answer by token, not in order, so the retry gets the same three again.
    pages = {
        None: places_fixture("text_search_page_1.json"),
        "radar-page-token-2": places_fixture("text_search_page_2.json"),
        "radar-page-token-3": places_fixture("text_search_page_3.json"),
    }
    mock_http.post(SEARCH_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=pages[json.loads(request.content).get("pageToken")]
        )
    )
    run_id = start_run(db, search_job, sales_user)

    real_checkpoint = tasks.checkpoint
    breaks = {"left": 1}

    def break_on_the_fourth_record(session: Session, run: JobRun, **kwargs: Any) -> None:
        if breaks["left"] and run.progress_done == 4:
            breaks["left"] -= 1
            # Poisoned the same way the real one was: the connection error aborts the
            # transaction, so the retry path could not write to this session either.
            try:
                session.execute(text("SELECT no_such_column FROM job_runs"))
            except Exception as exc:
                raise RuntimeError('prepared statement "_pg3_1" already exists') from exc
        real_checkpoint(session, run, **kwargs)

    monkeypatch.setattr(tasks, "checkpoint", break_on_the_fourth_record)

    delays: list[float] = []
    assert tasks.execute_job_run(run_id, sleeper=delays.append) is JobRunStatus.done

    assert breaks["left"] == 0, "the break never fired, so this proved nothing"
    assert delays, "the broken attempt has to be retried, not abandoned"
    run = reread(db, run_id)
    assert run.attempts == 2
    assert count(db, DiscoveredRecord) == TOTAL_RESULTS, "every result, not the first four"
    assert run.result_summary is not None
    assert run.result_summary["fetched"] == TOTAL_RESULTS


# --- the run that paged until the daily cap stopped it (v0.11.0) -------------------------


def short_pages_then_empty_forever() -> Any:
    """The 2026-09-24 live walk: 19, 16, 12, 4 and 1 places, then the recorded empty page
    — a token and no places — on every call after."""
    pool = [
        place
        for name in (
            "text_search_page_1.json",
            "text_search_page_2.json",
            "text_search_page_3.json",
        )
        for place in places_fixture(name)["places"]
    ]
    pages: list[dict[str, Any]] = []
    start = 0
    for number, size in enumerate((19, 16, 12, 4, 1), start=1):
        pages.append({"places": pool[start : start + size], "nextPageToken": f"walk-{number}"})
        start += size
    served = iter(pages)
    empty = places_fixture("text_search_empty_page_with_token_recorded.json")
    return lambda request: httpx.Response(200, json=next(served, None) or empty)


def places_used_today(redis: fakeredis.FakeStrictRedis, clock: FakeClock) -> int:
    cap = DailyCallCap(
        redis,
        source=GooglePlacesAdapter.name,
        cap=0,
        clock=lambda: datetime.fromtimestamp(clock(), tz=UTC),
    )
    return cap.used()


def test_a_walk_that_ends_in_empty_pages_finishes_instead_of_spending_the_day(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    fake_redis: fakeredis.FakeStrictRedis,
    clock: FakeClock,
) -> None:
    """The production bug end to end. Before the fix this run reached "52/52 finished"
    and then failed on the daily cap — 27, 70, 100 and 300 calls on four attempts."""
    route = mock_http.post(SEARCH_URL).mock(side_effect=short_pages_then_empty_forever())
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id) is JobRunStatus.done

    run = reread(db, run_id)
    assert route.call_count == 6
    assert count(db, ApiCall) == 6
    assert count(db, DiscoveredRecord) == 52
    assert run.result_summary is not None
    assert run.result_summary["fetched"] == 52
    assert run.result_summary["api_calls"] == 6
    assert places_used_today(fake_redis, clock) == 6


def test_the_safety_limit_fails_a_run_loudly_with_the_count(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    fake_redis: fakeredis.FakeStrictRedis,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run is stopped at its own limit — here 1 x 3 pages — long before the day's cap."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "places_run_call_cap_multiplier", 1)
    route = mock_http.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=places_fixture("text_search_page_1.json")),
            httpx.Response(500, json=places_fixture("error_500.json")),
            httpx.Response(500, json=places_fixture("error_500.json")),
            httpx.Response(200, json=places_fixture("text_search_page_2.json")),
        ]
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id, sleeper=lambda seconds: None) is JobRunStatus.failed

    run = reread(db, run_id)
    assert route.call_count == 3, "the fourth call was never sent"
    assert run.attempts == 1, "retrying a looping run only loops again"
    assert run.error is not None
    assert "RunCallCapExceededError" in run.error
    assert "Safety limit reached: this run made 3 'google_places' call(s)" in run.error
    assert "PLACES_RUN_CALL_CAP_MULTIPLIER" in run.error
    assert count(db, ApiCall) == 3
    assert count(db, DiscoveredRecord) == 20, "what the run found before the limit is kept"
    assert places_used_today(fake_redis, clock) == 3


def test_a_worker_retry_spends_from_the_same_safety_limit(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three 500s are transient, so the worker retries the run — on the calls it has left."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "places_run_call_cap_multiplier", 1)
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(500, json=places_fixture("error_500.json"))
    )
    run_id = start_run(db, search_job, sales_user)

    assert tasks.execute_job_run(run_id, sleeper=lambda seconds: None) is JobRunStatus.failed

    run = reread(db, run_id)
    assert route.call_count == 3, "the second attempt found the limit already spent"
    assert run.attempts == 2
    assert run.error is not None and "RunCallCapExceededError" in run.error
