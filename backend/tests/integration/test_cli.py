"""The operational commands: `sync-sources`, `purge-expired` and `places-smoke`."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli
from app.modules.adapters.base import RawDoc
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.adapters.google_places.client import PLACES_BASE_URL, TEXT_SEARCH_PATH
from app.modules.discovery.models import DiscoveredRecord
from app.modules.discovery.service import store_raw
from app.modules.sources.models import Source
from tests.conftest import places_fixture

SEARCH_URL = f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}"
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_the_usage_line_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2

    printed = capsys.readouterr().out
    for command in ("seed-admin", "sync-sources", "purge-expired", "places-smoke"):
        assert command in printed


def test_sync_sources_creates_the_rows_and_says_what_it_did(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["sync-sources"]) == 0

    db.expire_all()
    names = set(db.scalars(select(Source.name)))
    assert GooglePlacesAdapter.name in names
    printed = capsys.readouterr().out
    assert GooglePlacesAdapter.name in printed
    assert "enabled=True" in printed


def test_sync_sources_can_be_run_again_without_duplicating_anything(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["sync-sources"])
    db.expire_all()
    before = [row.id for row in db.scalars(select(Source))]

    cli.main(["sync-sources"])

    db.expire_all()
    assert [row.id for row in db.scalars(select(Source))] == before


def test_purge_expired_reports_what_it_dropped(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["sync-sources"])
    db.expire_all()
    source = db.scalars(select(Source).where(Source.name == GooglePlacesAdapter.name)).one()
    raw = RawDoc(
        source=GooglePlacesAdapter.name,
        source_record_id="ChIJold",
        source_url="https://www.google.com/maps/place/?q=place_id:ChIJold",
        payload={"id": "ChIJold"},
        fetched_at=NOW,
    )
    store_raw(
        db,
        source_id=source.id,
        raw=raw,
        content_ttl_days=30,
        now=datetime.now(UTC) - timedelta(days=40),
    )
    db.commit()

    assert cli.main(["purge-expired"]) == 0

    db.expire_all()
    record = db.scalars(select(DiscoveredRecord)).one()
    assert record.raw_payload is None
    assert record.source_record_id == "ChIJold"
    assert "1 expired record" in capsys.readouterr().out


def test_purge_expired_with_nothing_to_do_says_so(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["purge-expired"]) == 0
    assert "0 expired record" in capsys.readouterr().out


# --- places-smoke -------------------------------------------------------------------


def test_places_smoke_prints_a_table_and_stores_nothing(
    db: Session,
    mock_http: respx.MockRouter,
    capsys: pytest.CaptureFixture[str],
    places_adapter: GooglePlacesAdapter,
) -> None:
    """The smoke command is human-only and live; here it runs against a recording."""
    cli.main(["sync-sources"])
    mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_page_3.json"))
    )

    exit_code = cli.main(
        ["places-smoke", "--industry", "plumber", "--city", "Austin", "--state", "TX", "--max", "3"]
    )

    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "NAME" in printed and "WEBSITE" in printed
    assert "Austin Plumbing Co" in printed
    assert "Nothing was stored." in printed
    db.expire_all()
    assert list(db.scalars(select(DiscoveredRecord))) == []


def test_places_smoke_accepts_a_point_and_radius(
    db: Session,
    mock_http: respx.MockRouter,
    capsys: pytest.CaptureFixture[str],
    places_adapter: GooglePlacesAdapter,
) -> None:
    cli.main(["sync-sources"])
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_empty.json"))
    )

    exit_code = cli.main(
        [
            "places-smoke",
            "--industry",
            "plumber",
            "--lat",
            "30.26",
            "--lng",
            "-97.74",
            "--radius-m",
            "5000",
            "--max",
            "2",
        ]
    )

    assert exit_code == 0
    assert b"locationBias" in route.calls[0].request.read()
    assert "no results matched" in capsys.readouterr().out


def test_places_smoke_refuses_an_incomplete_area(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["places-smoke", "--industry", "plumber", "--city", "Austin"]) == 2
    assert "Bad search area" in capsys.readouterr().out


def test_places_smoke_refuses_to_run_without_a_key(
    db: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "google_places_api_key", SecretStr(""))

    exit_code = cli.main(
        ["places-smoke", "--industry", "plumber", "--city", "Austin", "--state", "TX"]
    )

    assert exit_code == 2
    assert "GOOGLE_PLACES_API_KEY is not set" in capsys.readouterr().out
