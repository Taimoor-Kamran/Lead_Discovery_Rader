"""The searches page's API (spec v0.8.0 §6): industries, cost estimate, max_results, the
daily-cap refusal, the pipeline view and the list with each job's last run."""

import uuid
from collections.abc import Iterator
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ratelimit import DailyCallCap
from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.auth.models import Role, User
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.jobs.service import (
    AUDIT_JOB_KIND,
    CLASSIFICATION_JOB_KIND,
    DISCOVERY_JOB_KIND,
    RESOLUTION_JOB_KIND,
    enqueue_run,
)
from app.modules.jobs.state import transition
from tests.conftest import auth_headers, geo_payload, make_user

API = "/api/v1"


def _job(test_client: TestClient, user: User, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Austin plumbers",
        "geo": geo_payload(),
        "industry": "plumber",
        "source_ids": [],
        "status": "active",
    }
    body.update(overrides)
    response = test_client.post(
        f"{API}/search-jobs", json=body, headers=auth_headers(test_client, user)
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# --- industries --------------------------------------------------------------------------------


def test_the_industry_dropdown_comes_from_the_taxonomy(
    client: TestClient, db: Session, sales_user: User
) -> None:
    response = client.get(f"{API}/search-jobs/industries", headers=auth_headers(client, sales_user))
    assert response.status_code == 200, response.text
    options = response.json()
    by_key = {option["key"]: option for option in options}
    assert by_key["plumbing"] == {"key": "plumbing", "label": "Plumbing", "query": "plumber"}
    assert by_key["hvac"]["query"] == "hvac contractor"
    assert by_key["general_contracting"]["label"] == "General contracting"
    assert [o["label"] for o in options] == sorted(o["label"] for o in options)
    assert len(by_key) == len(options), "one entry per slug"


# --- max_results -------------------------------------------------------------------------------


def test_max_results_is_stored_and_capped(
    client: TestClient, db: Session, sales_user: User
) -> None:
    created = _job(client, sales_user, max_results=40)
    assert created["max_results"] == 40

    too_many = client.post(
        f"{API}/search-jobs",
        json={
            "name": "x",
            "geo": geo_payload(),
            "industry": "plumber",
            "max_results": get_settings().places_max_results_per_job + 1,
        },
        headers=auth_headers(client, sales_user),
    )
    assert too_many.status_code == 422
    assert "PLACES_MAX_RESULTS_PER_JOB" in too_many.json()["error"]["message"]

    zero = client.post(
        f"{API}/search-jobs",
        json={"name": "x", "geo": geo_payload(), "industry": "plumber", "max_results": 0},
        headers=auth_headers(client, sales_user),
    )
    assert zero.status_code == 422


def test_the_discovery_worker_asks_for_the_job_max_results(
    client: TestClient,
    db: Session,
    sales_user: User,
    places_adapter: GooglePlacesAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter is handed `max_results` from the job, not the global cap."""
    from app.modules.adapters.base import DiscoveryConfig
    from app.modules.discovery.worker import run_discovery
    from app.modules.sources.service import get_source_by_name

    seen: list[int] = []

    def discover(self: GooglePlacesAdapter, cfg: DiscoveryConfig) -> Iterator[Any]:
        seen.append(cfg.max_results)
        return iter(())

    monkeypatch.setattr(GooglePlacesAdapter, "discover", discover)
    registry.sync_sources(db)
    db.commit()
    source = get_source_by_name(db, GOOGLE_PLACES)
    assert source is not None
    created = _job(client, sales_user, max_results=7, source_ids=[str(source.id)])

    run = enqueue_run(db, search_job_id=uuid.UUID(created["id"]), kind=DISCOVERY_JOB_KIND)
    transition(db, run, JobRunStatus.running)
    db.commit()
    run_discovery(db, run)

    assert seen == [7]


# --- cost estimate and the cap refusal ---------------------------------------------------------


def test_the_estimate_counts_places_pages_and_the_remaining_caps(
    client: TestClient, db: Session, sales_user: User, fake_redis: fakeredis.FakeStrictRedis
) -> None:
    DailyCallCap(fake_redis, source=GOOGLE_PLACES, cap=200).reserve()
    DailyCallCap(fake_redis, source=GOOGLE_PLACES, cap=200).reserve()

    response = client.post(
        f"{API}/search-jobs/estimate",
        json={"max_results": 45},
        headers=auth_headers(client, sales_user),
    )
    assert response.status_code == 200, response.text
    estimate = response.json()
    assert estimate["max_results"] == 45
    assert estimate["uses_places"] is True
    assert estimate["places_calls"] == 3, "ceil(45 / 20)"
    assert estimate["places_used_today"] == 2
    assert estimate["places_daily_cap"] == 200
    assert estimate["places_remaining_today"] == 198
    assert estimate["pagespeed_calls"] == 45
    assert estimate["pagespeed_remaining_today"] == 200
    assert estimate["ai_enabled"] is True and estimate["ai_provider"] == "fake"
    assert estimate["ai_calls"] == 45
    assert estimate["ai_budget_usd"] == 2.0 and estimate["ai_budget_remaining_usd"] == 2.0
    assert estimate["can_run"] is True and estimate["blockers"] == []

    default = client.post(
        f"{API}/search-jobs/estimate", json={}, headers=auth_headers(client, sales_user)
    ).json()
    assert default["max_results"] == get_settings().places_max_results_per_job


def test_a_run_is_refused_when_the_daily_cap_would_be_exceeded(
    client: TestClient,
    db: Session,
    sales_user: User,
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.FakeStrictRedis,
) -> None:
    monkeypatch.setenv("PLACES_DAILY_CALL_CAP", "3")
    get_settings.cache_clear()
    for _ in range(2):
        DailyCallCap(fake_redis, source=GOOGLE_PLACES, cap=3).reserve()
    created = _job(client, sales_user, max_results=40)  # 2 pages, 1 call left today
    headers = auth_headers(client, sales_user)

    estimate = client.get(f"{API}/search-jobs/{created['id']}/estimate", headers=headers).json()
    assert estimate["can_run"] is False
    assert estimate["places_remaining_today"] == 1
    assert "only 1 of today's 3 remain" in estimate["blockers"][0]

    refused = client.post(f"{API}/search-jobs/{created['id']}/run", headers=headers)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "daily_cap_exceeded"
    assert refused.json()["error"]["details"]["estimate"]["places_calls"] == 2
    assert db.query(JobRun).count() == 0, "nothing was queued"

    smaller = client.patch(
        f"{API}/search-jobs/{created['id']}", json={"max_results": 20}, headers=headers
    )
    assert smaller.status_code == 200
    accepted = client.post(f"{API}/search-jobs/{created['id']}/run", headers=headers)
    assert accepted.status_code == 202, accepted.text


# --- the pipeline view -------------------------------------------------------------------------


def _chain(db: Session, search_job_id: uuid.UUID) -> list[JobRun]:
    discovery = enqueue_run(db, search_job_id=search_job_id, kind=DISCOVERY_JOB_KIND)
    transition(db, discovery, JobRunStatus.running)
    discovery.result_summary = {"fetched": 12, "stored_new": 10, "updated": 2, "invalid": 0}
    transition(db, discovery, JobRunStatus.done)
    resolution = enqueue_run(
        db,
        search_job_id=search_job_id,
        kind=RESOLUTION_JOB_KIND,
        params={"parent_run_id": str(discovery.id)},
    )
    transition(db, resolution, JobRunStatus.running)
    resolution.result_summary = {"processed": 12, "created": 9, "linked_existing": 3}
    transition(db, resolution, JobRunStatus.done)
    audit = enqueue_run(
        db,
        search_job_id=search_job_id,
        kind=AUDIT_JOB_KIND,
        params={"parent_run_id": str(resolution.id)},
    )
    transition(db, audit, JobRunStatus.running)
    transition(db, audit, JobRunStatus.failed, error="QuotaExceededError: PageSpeed cap")
    db.commit()
    return [discovery, resolution, audit]


def test_the_pipeline_view_follows_the_four_stages(
    client: TestClient, db: Session, sales_user: User
) -> None:
    created = _job(client, sales_user)
    discovery, resolution, _audit = _chain(db, uuid.UUID(created["id"]))
    headers = auth_headers(client, sales_user)

    response = client.get(f"{API}/search-jobs/{created['id']}/pipeline", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["discovery_run_id"] == str(discovery.id)
    assert [stage["stage"] for stage in body["stages"]] == [
        "discovery",
        "resolution",
        "audit",
        "classification",
    ]
    by_stage = {stage["stage"]: stage for stage in body["stages"]}
    assert by_stage["discovery"]["run"]["status"] == "done"
    assert by_stage["discovery"]["counts"] == {
        "fetched": 12,
        "stored_new": 10,
        "updated": 2,
        "invalid": 0,
    }
    assert by_stage["resolution"]["run"]["id"] == str(resolution.id)
    assert by_stage["resolution"]["counts"]["created"] == 9
    assert by_stage["audit"]["run"]["status"] == "failed"
    assert by_stage["audit"]["error"] == "QuotaExceededError: PageSpeed cap"
    assert by_stage["classification"] == {
        "stage": CLASSIFICATION_JOB_KIND,
        "run": None,
        "counts": {},
        "error": None,
    }
    assert body["review_queue_query"] == {"city": "Austin"}

    # An explicit run id works too, and a foreign one is a 404.
    again = client.get(
        f"{API}/search-jobs/{created['id']}/pipeline",
        params={"run_id": str(discovery.id)},
        headers=headers,
    ).json()
    assert again["discovery_run_id"] == str(discovery.id)
    foreign = client.get(
        f"{API}/search-jobs/{created['id']}/pipeline",
        params={"run_id": str(resolution.id)},
        headers=headers,
    )
    assert foreign.status_code == 404


def test_a_search_without_runs_has_an_empty_pipeline(
    client: TestClient, db: Session, sales_user: User
) -> None:
    created = _job(client, sales_user, geo={"lat": 30.2, "lng": -97.7, "radius_m": 5000})
    body = client.get(
        f"{API}/search-jobs/{created['id']}/pipeline", headers=auth_headers(client, sales_user)
    ).json()
    assert body["discovery_run_id"] is None
    assert all(stage["run"] is None for stage in body["stages"])
    assert body["review_queue_query"] == {}, "no city to filter on"


def test_the_list_carries_each_search_last_run(
    client: TestClient, db: Session, sales_user: User
) -> None:
    older = _job(client, sales_user, name="older")
    _job(client, sales_user, name="newer")
    discovery, _, _ = _chain(db, uuid.UUID(older["id"]))

    body = client.get(f"{API}/search-jobs", headers=auth_headers(client, sales_user)).json()
    by_name = {item["name"]: item for item in body["items"]}
    assert by_name["older"]["last_run"]["id"] == str(discovery.id)
    assert by_name["older"]["last_run"]["status"] == "done"
    assert by_name["newer"]["last_run"] is None
    assert list(by_name) == ["newer", "older"], "newest first"


# --- who may do what ---------------------------------------------------------------------------


def test_a_tech_admin_reads_but_does_not_create_or_run(
    client: TestClient, db: Session, sales_user: User
) -> None:
    created = _job(client, sales_user)
    tech = make_user(db, Role.tech_admin)
    headers = auth_headers(client, tech)

    assert client.get(f"{API}/search-jobs", headers=headers).status_code == 200
    assert client.get(f"{API}/search-jobs/industries", headers=headers).status_code == 200
    assert client.post(f"{API}/search-jobs/estimate", json={}, headers=headers).status_code == 200
    assert (
        client.get(f"{API}/search-jobs/{created['id']}/pipeline", headers=headers).status_code
        == 200
    )
    assert client.post(f"{API}/search-jobs/{created['id']}/run", headers=headers).status_code == 403
    assert (
        client.post(
            f"{API}/search-jobs",
            json={"name": "x", "geo": geo_payload(), "industry": "plumber"},
            headers=headers,
        ).status_code
        == 403
    )
