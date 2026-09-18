"""The read endpoints over what discovery produced, and who may see what."""

import uuid

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.adapters.google_places.client import PLACES_BASE_URL, TEXT_SEARCH_PATH
from app.modules.auth.models import Role, User
from app.modules.jobs.models import JobRunStatus, SearchJob, SearchJobStatus
from app.modules.jobs.service import DISCOVERY_JOB_KIND, enqueue_run
from app.modules.sources.models import Source
from app.workers import tasks
from tests.conftest import auth_headers, make_user, places_fixture

SEARCH_URL = f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}"


@pytest.fixture
def places_source(db: Session, places_adapter: GooglePlacesAdapter) -> Source:
    sources = registry.sync_sources(db)
    db.commit()
    return next(s for s in sources if s.name == GooglePlacesAdapter.name)


@pytest.fixture
def finished_run(
    db: Session, mock_http: respx.MockRouter, places_source: Source, sales_user: User
) -> tuple[SearchJob, uuid.UUID]:
    """One completed run holding the twenty records of the recorded third page."""
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
    run = enqueue_run(db, search_job_id=job.id, kind=DISCOVERY_JOB_KIND, actor_id=sales_user.id)
    db.commit()
    assert tasks.execute_job_run(run.id) is JobRunStatus.done
    return job, run.id


# --- GET /jobs/{id}/records ---------------------------------------------------------


def test_the_records_of_a_run_are_listed_with_their_mapped_fields(
    client: TestClient, db: Session, sales_user: User, finished_run: tuple[SearchJob, uuid.UUID]
) -> None:
    _, run_id = finished_run

    response = client.get(
        f"/api/v1/jobs/{run_id}/records?limit=100", headers=auth_headers(client, sales_user)
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 20
    named = next(i for i in items if i["display_name"])
    assert named["source"] == "google_places"
    assert named["source_record_id"].startswith("ChIJradar")
    assert named["source_url"].endswith(named["source_record_id"])
    assert named["formatted_address"] and named["phone"] and named["website"]
    assert "raw_payload" not in named, "the payload belongs to the detail view"


def test_a_record_with_only_an_id_lists_with_nulls_not_placeholders(
    client: TestClient, db: Session, sales_user: User, finished_run: tuple[SearchJob, uuid.UUID]
) -> None:
    _, run_id = finished_run

    items = client.get(
        f"/api/v1/jobs/{run_id}/records?limit=100", headers=auth_headers(client, sales_user)
    ).json()["items"]

    minimal = next(i for i in items if i["source_record_id"] == "ChIJradar0059MinimalPlace")
    assert minimal["display_name"] is None
    assert minimal["formatted_address"] is None
    assert minimal["phone"] is None
    assert minimal["website"] is None
    assert minimal["business_status"] is None


def test_the_record_list_pages_with_a_cursor(
    client: TestClient, db: Session, sales_user: User, finished_run: tuple[SearchJob, uuid.UUID]
) -> None:
    _, run_id = finished_run
    headers = auth_headers(client, sales_user)

    first = client.get(f"/api/v1/jobs/{run_id}/records?limit=15", headers=headers).json()
    assert len(first["items"]) == 15
    assert first["next_cursor"]

    second = client.get(
        f"/api/v1/jobs/{run_id}/records?limit=15&cursor={first['next_cursor']}", headers=headers
    ).json()
    assert len(second["items"]) == 5
    assert second["next_cursor"] is None
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


def test_records_of_an_unknown_run_are_404(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.get(
        f"/api/v1/jobs/{uuid.uuid4()}/records", headers=auth_headers(client, sales_user)
    )

    assert response.status_code == 404


def test_the_record_list_requires_authentication(
    client: TestClient, db: Session, finished_run: tuple[SearchJob, uuid.UUID]
) -> None:
    _, run_id = finished_run

    assert client.get(f"/api/v1/jobs/{run_id}/records").status_code == 401


# --- GET /discovered-records/{id} ---------------------------------------------------


def test_a_reviewer_sees_the_full_record_with_its_payload_and_sightings(
    client: TestClient, db: Session, sales_user: User, finished_run: tuple[SearchJob, uuid.UUID]
) -> None:
    job, run_id = finished_run
    reviewer = make_user(db, Role.reviewer)
    headers = auth_headers(client, reviewer)
    record_id = client.get(f"/api/v1/jobs/{run_id}/records?limit=100", headers=headers).json()[
        "items"
    ][0]["id"]

    response = client.get(f"/api/v1/discovered-records/{record_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["raw_payload"]["id"] == body["source_record_id"]
    assert body["payload_hash"]
    assert body["content_expires_at"]
    assert body["business_id"] is None
    assert body["confidence"] is None
    assert body["evidence_text"] is None
    assert len(body["sightings"]) == 1
    assert body["sightings"][0]["job_run_id"] == str(run_id)
    assert body["sightings"][0]["search_job_id"] == str(job.id)


def test_a_sales_rep_may_not_open_a_discovered_record(
    client: TestClient, db: Session, sales_user: User, finished_run: tuple[SearchJob, uuid.UUID]
) -> None:
    _, run_id = finished_run
    headers = auth_headers(client, sales_user)
    record_id = client.get(f"/api/v1/jobs/{run_id}/records?limit=100", headers=headers).json()[
        "items"
    ][0]["id"]

    response = client.get(f"/api/v1/discovered-records/{record_id}", headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_an_unknown_record_is_404(client: TestClient, db: Session, admin_user: User) -> None:
    response = client.get(
        f"/api/v1/discovered-records/{uuid.uuid4()}", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 404


# --- GET /search-jobs/{id}/runs -----------------------------------------------------


def test_the_runs_of_a_search_job_are_listed_newest_first(
    client: TestClient,
    db: Session,
    mock_http: respx.MockRouter,
    sales_user: User,
    finished_run: tuple[SearchJob, uuid.UUID],
) -> None:
    job, first_run_id = finished_run
    headers = auth_headers(client, sales_user)
    mock_http.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    second = client.post(f"/api/v1/search-jobs/{job.id}/run", headers=headers).json()["id"]

    response = client.get(f"/api/v1/search-jobs/{job.id}/runs", headers=headers)

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert ids == [second, str(first_run_id)]
    finished = response.json()["items"][1]
    assert finished["status"] == JobRunStatus.done.value
    assert finished["result_summary"]["stored_new"] == 20


def test_the_runs_of_an_unknown_search_job_are_404(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.get(
        f"/api/v1/search-jobs/{uuid.uuid4()}/runs", headers=auth_headers(client, sales_user)
    )

    assert response.status_code == 404


def test_the_run_list_requires_authentication(
    client: TestClient, db: Session, finished_run: tuple[SearchJob, uuid.UUID]
) -> None:
    job, _ = finished_run

    assert client.get(f"/api/v1/search-jobs/{job.id}/runs").status_code == 401
