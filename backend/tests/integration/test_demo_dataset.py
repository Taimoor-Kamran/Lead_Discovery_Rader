"""The demo fixture, loaded and resolved, against its expected-outcomes file.

This is the acceptance test for entity resolution as a whole. The expectations live in
`austin_plumbers.expected.json` and were written from how the fixture was designed, so a
change to a weight, a threshold or a hard rule fails here rather than quietly producing a
different set of businesses.
"""

import json
import os
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.demo.loader import DemoLoadResult, load_demo_data
from app.modules.adapters import registry
from app.modules.adapters.demo_fixture import SOURCE_NAME, demo_places
from app.modules.auth.models import Role
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.resolution.models import MatchCandidate, MatchCandidateStatus, ResolutionStatus
from app.modules.sources.models import Source
from app.workers import tasks
from tests.conftest import make_user

EXPECTED = json.loads(
    (
        Path(__file__).resolve().parents[2] / "app" / "demo" / "austin_plumbers.expected.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture
def development() -> Iterator[None]:
    """Run as a developer's machine would, where the demo source exists.

    The environment is restored by hand rather than with `monkeypatch`, because the
    settings cache has to be cleared *after* the variables are back — a teardown order
    `monkeypatch` does not give us, and a leaked `development` would quietly switch the
    demo source on for every test that ran afterwards.
    """
    previous = {name: os.environ.get(name) for name in ("APP_ENV", "ENVIRONMENT")}
    os.environ.update({"APP_ENV": "development", "ENVIRONMENT": "development"})
    get_settings.cache_clear()
    registry.reload_builtins()
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        registry.reload_builtins()


@pytest.fixture
def loaded(db: Session, development: None) -> DemoLoadResult:
    """The whole fixture, discovered and then resolved to completion."""
    make_user(db, Role.admin)
    result = load_demo_data(db)
    assert tasks.execute_job_run(result.resolution_run_id) is JobRunStatus.done
    db.expire_all()
    return result


def record_by_id(session: Session, source_record_id: str) -> DiscoveredRecord:
    return session.scalars(
        select(DiscoveredRecord).where(DiscoveredRecord.source_record_id == source_record_id)
    ).one()


def business_of(session: Session, source_record_id: str) -> Business | None:
    record = record_by_id(session, source_record_id)
    return session.get(Business, record.business_id) if record.business_id else None


def summary_of(session: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = session.get(JobRun, run_id)
    assert run is not None
    return dict(run.result_summary or {})


# --- the headline numbers -----------------------------------------------------------


def test_the_fixture_holds_the_expected_number_of_records() -> None:
    assert len(demo_places()) == EXPECTED["records"]


def test_the_run_summary_matches_the_expected_file(db: Session, loaded: DemoLoadResult) -> None:
    assert summary_of(db, loaded.resolution_run_id) == EXPECTED["run_summary"]


def test_the_business_count_matches_the_expected_file(db: Session, loaded: DemoLoadResult) -> None:
    assert len(list(db.scalars(select(Business)))) == EXPECTED["businesses"]


def test_every_record_is_resolved_and_none_is_invalid(db: Session, loaded: DemoLoadResult) -> None:
    statuses = Counter(r.resolution_status for r in db.scalars(select(DiscoveredRecord)))

    assert statuses[ResolutionStatus.pending] == 0
    assert statuses[ResolutionStatus.invalid] == 0
    assert statuses[ResolutionStatus.needs_review] == len(EXPECTED["needs_review"]["records"])


# --- the deliberate cases -----------------------------------------------------------


@pytest.mark.parametrize("pair", EXPECTED["auto_merged"]["pairs"])
def test_an_exact_duplicate_is_auto_merged(
    db: Session, loaded: DemoLoadResult, pair: list[str]
) -> None:
    first, second = (record_by_id(db, place_id) for place_id in pair)

    assert first.business_id is not None
    assert first.business_id == second.business_id
    assert first.resolution_status is ResolutionStatus.linked
    assert second.resolution_status is ResolutionStatus.linked


@pytest.mark.parametrize("case", EXPECTED["needs_review"]["records"])
def test_a_mid_confidence_pair_waits_for_a_human_and_creates_no_business(
    db: Session, loaded: DemoLoadResult, case: dict[str, str]
) -> None:
    record = record_by_id(db, case["record"])
    against = record_by_id(db, case["against"])

    assert record.resolution_status is ResolutionStatus.needs_review
    assert record.business_id is None, "a pending pair never creates or joins a business"

    candidate = db.scalars(
        select(MatchCandidate).where(MatchCandidate.discovered_record_id == record.id)
    ).one()
    assert candidate.status is MatchCandidateStatus.pending
    assert candidate.business_id == against.business_id


@pytest.mark.parametrize("pair", EXPECTED["kept_apart"]["pairs"])
def test_one_name_in_two_cities_stays_two_businesses(
    db: Session, loaded: DemoLoadResult, pair: list[str]
) -> None:
    first, second = (record_by_id(db, place_id) for place_id in pair)

    assert first.business_id is not None
    assert second.business_id is not None
    assert first.business_id != second.business_id
    assert (
        db.scalars(
            select(MatchCandidate).where(
                MatchCandidate.discovered_record_id.in_([first.id, second.id])
            )
        ).first()
        is None
    ), "they share nothing, so they are never even candidates"


def test_the_pending_queue_is_exactly_what_the_expected_file_says(
    db: Session, loaded: DemoLoadResult
) -> None:
    pending = list(
        db.scalars(
            select(MatchCandidate).where(MatchCandidate.status == MatchCandidateStatus.pending)
        )
    )

    assert len(pending) == EXPECTED["pending_match_candidates"]


def test_chain_locations_sharing_only_a_domain_never_auto_merge(
    db: Session, loaded: DemoLoadResult
) -> None:
    chain = [
        case["record"]
        for case in EXPECTED["needs_review"]["records"]
        if case["kind"] == "chain_location"
    ]

    for place_id in chain:
        record = record_by_id(db, place_id)
        candidate = db.scalars(
            select(MatchCandidate).where(MatchCandidate.discovered_record_id == record.id)
        ).one()
        assert candidate.status is MatchCandidateStatus.pending
        assert float(candidate.score) < get_settings().resolution_auto_merge
        assert candidate.signals["domain_match"] == 1.0


# --- website kinds and messy inputs -------------------------------------------------


@pytest.mark.parametrize("place_id", EXPECTED["website_kinds"]["own_site"])
def test_an_ordinary_site_is_its_own(db: Session, loaded: DemoLoadResult, place_id: str) -> None:
    business = business_of(db, place_id)

    assert business is not None
    assert business.website_kind is WebsiteKind.own_site
    assert business.domain


@pytest.mark.parametrize(
    ("place_id", "domain"), sorted(EXPECTED["website_kinds"]["builder_subdomain"].items())
)
def test_a_builder_subdomain_keeps_its_whole_host(
    db: Session, loaded: DemoLoadResult, place_id: str, domain: str
) -> None:
    business = business_of(db, place_id)

    assert business is not None
    assert business.website_kind is WebsiteKind.builder_subdomain
    assert business.domain == domain


@pytest.mark.parametrize("place_id", EXPECTED["website_kinds"]["social_profile"])
def test_a_social_profile_has_no_domain(db: Session, loaded: DemoLoadResult, place_id: str) -> None:
    business = business_of(db, place_id)

    assert business is not None
    assert business.website_kind is WebsiteKind.social_profile
    assert business.domain is None
    assert business.website is not None, "the profile is still recorded, just never a key"


@pytest.mark.parametrize("place_id", EXPECTED["website_kinds"]["none"])
def test_no_website_means_no_domain(db: Session, loaded: DemoLoadResult, place_id: str) -> None:
    business = business_of(db, place_id)

    assert business is not None
    assert business.website_kind is WebsiteKind.none
    assert business.website is None
    assert business.domain is None


@pytest.mark.parametrize(
    ("place_id", "expected"), sorted(EXPECTED["normalization"]["records"].items())
)
def test_messy_input_normalizes_to_the_expected_values(
    db: Session, loaded: DemoLoadResult, place_id: str, expected: dict[str, Any]
) -> None:
    business = business_of(db, place_id)

    assert business is not None
    for field, value in expected.items():
        actual = getattr(business, field)
        if isinstance(actual, BusinessStatus | WebsiteKind):
            actual = actual.value
        assert actual == value, f"{place_id}.{field}"


# --- the source itself --------------------------------------------------------------


def test_the_demo_source_is_registered_only_in_development(db: Session, development: None) -> None:
    registry.sync_sources(db)
    db.commit()

    assert db.scalars(select(Source).where(Source.name == SOURCE_NAME)).first() is not None


def test_the_demo_source_does_not_exist_outside_development(db: Session) -> None:
    """The suite runs as `ci`, which is exactly the point being asserted."""
    assert get_settings().is_development is False
    registry.reload_builtins()
    registry.sync_sources(db)
    db.commit()

    assert SOURCE_NAME not in registry.names()
    assert db.scalars(select(Source).where(Source.name == SOURCE_NAME)).first() is None


def test_demo_records_are_flagged_so_they_are_never_exported(
    db: Session, loaded: DemoLoadResult
) -> None:
    source = db.scalars(select(Source).where(Source.name == SOURCE_NAME)).one()

    assert source.config["exclude_from_crm_export"] is True


def test_loading_outside_development_is_refused(db: Session) -> None:
    from app.core.errors import ValidationFailedError

    make_user(db, Role.admin)
    with pytest.raises(ValidationFailedError):
        load_demo_data(db)


def test_loading_without_an_admin_says_so(db: Session, development: None) -> None:
    from app.core.errors import ValidationFailedError

    make_user(db, Role.sales_rep)
    with pytest.raises(ValidationFailedError, match="seed-admin"):
        load_demo_data(db)


# --- idempotency --------------------------------------------------------------------


def test_loading_the_demo_data_twice_changes_nothing(
    db: Session, loaded: DemoLoadResult, development: None
) -> None:
    before = _snapshot(db)

    again = load_demo_data(db)
    assert tasks.execute_job_run(again.resolution_run_id) is JobRunStatus.done
    db.expire_all()

    assert again.search_job_id == loaded.search_job_id
    assert again.discovery_run_id == loaded.discovery_run_id
    assert _snapshot(db) == before


def test_resolving_the_same_run_again_changes_nothing(
    db: Session, loaded: DemoLoadResult, development: None
) -> None:
    from app.modules.resolution.service import enqueue_resolution

    before = _snapshot(db)

    second = enqueue_resolution(db, loaded.discovery_run_id, idempotency_key="second-pass")
    assert tasks.execute_job_run(second.id) is JobRunStatus.done
    db.expire_all()

    assert _snapshot(db) == before
    assert summary_of(db, second.id)["created"] == 0
    assert summary_of(db, second.id)["needs_review"] == 0, "pairs awaiting a human are left alone"


def _snapshot(session: Session) -> dict[str, Any]:
    """Everything resolution owns, in a form two runs can be compared by."""
    return {
        "businesses": sorted(
            (str(b.id), b.display_name, b.domain, b.phone_e164)
            for b in session.scalars(select(Business))
        ),
        "links": sorted(
            (
                r.source_record_id,
                str(r.business_id) if r.business_id else None,
                r.resolution_status.value,
            )
            for r in session.scalars(select(DiscoveredRecord))
        ),
        "candidates": sorted(
            (str(c.discovered_record_id), str(c.business_id), c.status.value)
            for c in session.scalars(select(MatchCandidate))
        ),
    }


def test_a_user_and_the_expected_file_agree_on_the_search_job(
    db: Session, loaded: DemoLoadResult
) -> None:
    from app.modules.jobs.models import SearchJob

    job = db.get(SearchJob, loaded.search_job_id)
    assert job is not None
    assert job.industry == "plumber"
    assert job.geo == {"city": "Austin", "state": "TX"}
