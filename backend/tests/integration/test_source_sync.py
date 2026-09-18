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


def test_sync_sources_creates_one_row_per_adapter(db: Session) -> None:
    registry.register(StubAdapter())

    rows = registry.sync_sources(db)
    db.commit()

    by_name = {row.name: row for row in rows}
    assert set(by_name) == set(registry.names())
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
