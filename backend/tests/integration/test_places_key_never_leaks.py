"""The Places API key must not survive anywhere a human or a backup can read it.

The test key is a sentinel literal, so this is a search for an exact string rather than a
judgement call: if it appears in a log line, an `api_calls` row, a job error or a stored
payload, the test fails.
"""

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.adapters.google_places.client import PLACES_BASE_URL, TEXT_SEARCH_PATH
from app.modules.auth.models import User
from app.modules.discovery.models import ApiCall, DiscoveredRecord
from app.modules.jobs.models import JobRun, JobRunStatus, SearchJob, SearchJobStatus
from app.modules.jobs.service import DISCOVERY_JOB_KIND, enqueue_run
from app.modules.sources.models import Source
from app.workers import tasks
from tests.conftest import TEST_PLACES_API_KEY, places_fixture
from tests.integration.test_no_secrets_in_logs import CapturingHandler, captured_logs  # noqa: F401

SEARCH_URL = f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}"


@pytest.fixture
def search_job(db: Session, sales_user: User, places_adapter: GooglePlacesAdapter) -> SearchJob:
    sources = registry.sync_sources(db)
    db.commit()
    source: Source = next(s for s in sources if s.name == GooglePlacesAdapter.name)
    job = SearchJob(
        name="Austin plumbers",
        geo={"city": "Austin", "state": "TX"},
        industry="plumber",
        source_ids=[source.id],
        status=SearchJobStatus.active,
        created_by=sales_user.id,
    )
    db.add(job)
    db.commit()
    return job


def run_it(db: Session, job: SearchJob, user: User) -> JobRun:
    run = enqueue_run(db, search_job_id=job.id, kind=DISCOVERY_JOB_KIND, actor_id=user.id)
    db.commit()
    tasks.execute_job_run(run.id, sleeper=lambda seconds: None)
    db.expire_all()
    stored = db.get(JobRun, run.id)
    assert stored is not None
    return stored


def everything_stored(db: Session) -> str:
    """Every column of every row this spec writes, as one searchable blob."""
    parts: list[Any] = []
    for call in db.scalars(select(ApiCall)):
        parts += [call.endpoint, call.status_code, call.error_class]
    for record in db.scalars(select(DiscoveredRecord)):
        parts += [record.source_url, record.source_record_id, record.raw_payload]
    for run in db.scalars(select(JobRun)):
        parts += [run.error, run.result_summary]
    return " ".join(str(p) for p in parts)


def test_a_successful_run_leaves_the_key_nowhere(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    captured_logs: CapturingHandler,  # noqa: F811
) -> None:
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_page_3.json"))
    )

    run = run_it(db, search_job, sales_user)

    assert run.status is JobRunStatus.done
    assert TEST_PLACES_API_KEY not in "\n".join(captured_logs.lines)
    assert TEST_PLACES_API_KEY not in everything_stored(db)


def test_a_provider_error_that_echoes_the_key_is_redacted_everywhere(
    db: Session,
    mock_http: respx.MockRouter,
    search_job: SearchJob,
    sales_user: User,
    captured_logs: CapturingHandler,  # noqa: F811
) -> None:
    """Google sometimes quotes the offending key straight back at you."""
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": f"API key not valid. Key: {TEST_PLACES_API_KEY}",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )
    )

    run = run_it(db, search_job, sales_user)

    assert run.status is JobRunStatus.failed
    assert run.error is not None
    assert TEST_PLACES_API_KEY not in run.error
    assert "[REDACTED]" in run.error
    assert TEST_PLACES_API_KEY not in "\n".join(captured_logs.lines)
    assert TEST_PLACES_API_KEY not in everything_stored(db)


def test_the_key_is_scrubbed_even_when_something_logs_it_outright(
    captured_logs: CapturingHandler,  # noqa: F811
) -> None:
    import logging

    logging.getLogger("app.test").warning(
        "calling places with %s", TEST_PLACES_API_KEY, extra={"api_key": TEST_PLACES_API_KEY}
    )

    output = "\n".join(captured_logs.lines)
    assert TEST_PLACES_API_KEY not in output
    assert "[REDACTED]" in output


def test_an_auth_failure_says_what_to_check_without_quoting_the_key(
    db: Session, mock_http: respx.MockRouter, search_job: SearchJob, sales_user: User
) -> None:
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(403, json=places_fixture("error_403.json"))
    )

    run = run_it(db, search_job, sales_user)

    assert run.error is not None
    assert "GOOGLE_PLACES_API_KEY" in run.error, "the variable name, not its value"
    assert TEST_PLACES_API_KEY not in run.error
