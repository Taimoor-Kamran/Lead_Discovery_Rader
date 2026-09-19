"""`sync-sources`: the registry is the source of truth for the `sources` table."""

from sqlalchemy.orm import Session

from app.modules.adapters import registry
from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
from app.modules.sources.models import SourceKind
from tests.unit.test_adapter_registry import StubAdapter


def test_the_google_places_row_carries_its_terms_and_retention(db: Session) -> None:
    [places] = [s for s in registry.sync_sources(db) if s.name == GOOGLE_PLACES]
    db.commit()

    assert places.kind is SourceKind.api
    assert places.enabled is True
    assert places.config["terms_url"].startswith("https://")
    assert places.config["content_ttl_days"] == 30
    assert places.config["commercial_use_note"]


def test_sync_sources_creates_one_row_per_adapter_and_service(db: Session) -> None:
    registry.register(StubAdapter())

    rows = registry.sync_sources(db)
    db.commit()

    by_name = {row.name: row for row in rows}
    service_names = {spec.name for spec in registry.service_sources()}
    assert set(by_name) == set(registry.names()) | service_names
    stub = by_name["stub"]
    assert stub.enabled is True
    assert stub.kind is SourceKind.feed
    assert stub.config["display_name"] == "Stub"
    assert stub.config["content_ttl_days"] == 7
    assert stub.config["rate_limit"] == {
        "requests_per_second": 1.0,
        "burst": 2,
        "daily_call_cap": 10,
    }


def test_the_pagespeed_row_is_marked_as_a_service_rather_than_a_searchable_source(
    db: Session,
) -> None:
    """It has a row so its calls are metered, but a search job may not name it."""
    from app.core.errors import ValidationFailedError
    from app.modules.audit_web.psi import PAGESPEED_SOURCE_NAME
    from app.modules.sources.service import validate_source_ids

    [psi] = [s for s in registry.sync_sources(db) if s.name == PAGESPEED_SOURCE_NAME]
    db.commit()

    assert psi.kind is SourceKind.api
    assert psi.config["role"] == "audit_service"
    assert psi.config["rate_limit"]["daily_call_cap"] == 200

    try:
        validate_source_ids(db, [psi.id])
    except ValidationFailedError as exc:
        assert exc.details["service_sources"] == [PAGESPEED_SOURCE_NAME]
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("PageSpeed must not be selectable as a search-job source")


def test_sync_sources_is_idempotent(db: Session) -> None:
    registry.sync_sources(db)
    db.commit()
    first = {row.id for row in registry.sync_sources(db)}
    db.commit()

    second = {row.id for row in registry.sync_sources(db)}
    db.commit()

    assert first == second


def test_sync_sources_refreshes_metadata_but_keeps_the_operators_choice(db: Session) -> None:
    registry.register(StubAdapter(ttl_days=7))
    [stub] = [s for s in registry.sync_sources(db) if s.name == "stub"]
    stub.enabled = False
    db.commit()

    registry.register(StubAdapter(ttl_days=90), replace=True)
    [synced] = [s for s in registry.sync_sources(db) if s.name == "stub"]
    db.commit()

    assert synced.config["content_ttl_days"] == 90
    assert synced.enabled is False, "sync-sources must never re-enable a disabled source"
