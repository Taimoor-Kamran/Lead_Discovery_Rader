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
from app.modules.businesses.models import Business
from app.modules.discovery.models import DiscoveredRecord
from app.modules.discovery.service import store_raw
from app.modules.normalization.schemas import BusinessStatus, NormalizedBusiness, WebsiteKind
from app.modules.sources.models import Source
from tests.conftest import places_fixture

SEARCH_URL = f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}"
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_the_usage_line_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2

    printed = capsys.readouterr().out
    for command in (
        "seed-admin",
        "sync-sources",
        "purge-expired",
        "recompute-businesses",
        "places-smoke",
        "ai-smoke",
        "load-demo-data",
    ):
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


# --- ai-smoke -----------------------------------------------------------------------


def test_ai_smoke_refuses_to_run_without_a_key(
    db: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr(""))

    assert cli.main(["ai-smoke"]) == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().out


def test_ai_smoke_refuses_to_run_without_a_model_name(
    db: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-test"))
    monkeypatch.setattr(get_settings(), "ai_triage_model", "")

    assert cli.main(["ai-smoke"]) == 2
    assert "AI_TRIAGE_MODEL" in capsys.readouterr().out


def test_ai_smoke_needs_an_audited_business(
    db: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a key and a model but an empty database, it stops before any call is made."""
    from pydantic import SecretStr

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-test"))
    monkeypatch.setattr(get_settings(), "ai_triage_model", "some-model")

    assert cli.main(["ai-smoke"]) == 2
    assert "No audited business" in capsys.readouterr().out


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


# --- recompute-businesses (v0.4.0) ----------------------------------------------------


def demo_source_row(session: Session) -> Source:
    from app.modules.adapters import registry
    from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES

    registry.sync_sources(session)
    session.flush()
    return session.scalars(select(Source).where(Source.name == GOOGLE_PLACES)).one()


def linked_record(session: Session, business: Business, place_id: str) -> DiscoveredRecord:
    record = DiscoveredRecord(
        source_id=demo_source_row(session).id,
        source_record_id=place_id,
        raw_payload={"id": place_id},
        payload_hash="hash",
        first_discovered_at=NOW,
        last_discovered_at=NOW,
        business_id=business.id,
    )
    session.add(record)
    session.flush()
    return record


def normalized(
    *, name: str, website: str | None, domain: str | None, kind: WebsiteKind
) -> NormalizedBusiness:
    return NormalizedBusiness(
        source="google_places",
        display_name=name,
        normalized_name=name.lower(),
        industry="plumbing",
        website=website,
        domain=domain,
        website_kind=kind,
        business_status=BusinessStatus.operational,
    )


def seed_oak_hill(session: Session) -> Business:
    """The v0.3.0 bug: one record has the real site, the other only a Facebook page.

    The stored row is then put back into the mixed state that survivorship used to
    produce — a Facebook URL beside the real domain, a web presence no source reported.
    """
    from app.modules.resolution import survivorship

    business = Business(display_name="Oak Hill Plumbing Group")
    session.add(business)
    session.flush()

    own = linked_record(session, business, "oak-hill-own-site")
    survivorship.write_field_values(
        session,
        business=business,
        record=own,
        normalized=normalized(
            name="Oak Hill Plumbing Group",
            website="https://oakhillplumbing.invalid/",
            domain="oakhillplumbing.invalid",
            kind=WebsiteKind.own_site,
        ),
        observed_at=NOW,
    )
    social = linked_record(session, business, "oak-hill-facebook")
    survivorship.write_field_values(
        session,
        business=business,
        record=social,
        normalized=normalized(
            name="Oak Hill Plumbing",
            website="https://www.facebook.com/oakhillplumbing",
            domain=None,
            kind=WebsiteKind.social_profile,
        ),
        observed_at=NOW,
    )

    business.website = "https://www.facebook.com/oakhillplumbing"
    business.domain = "oakhillplumbing.invalid"
    business.website_kind = WebsiteKind.social_profile
    session.commit()
    return business


def test_recompute_businesses_fixes_a_row_that_disagrees_with_survivorship(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    business = seed_oak_hill(db)

    assert cli.main(["recompute-businesses"]) == 0

    db.expire_all()
    db.refresh(business)
    assert business.website == "https://oakhillplumbing.invalid/"
    assert business.domain == "oakhillplumbing.invalid"
    assert business.website_kind is WebsiteKind.own_site

    printed = capsys.readouterr().out
    assert "1 changed" in printed
    assert "0 unchanged" in printed


def test_recompute_businesses_is_a_no_op_the_second_time(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_oak_hill(db)
    cli.main(["recompute-businesses"])
    capsys.readouterr()

    assert cli.main(["recompute-businesses"]) == 0

    printed = capsys.readouterr().out
    assert "0 changed" in printed
    assert "1 unchanged" in printed


def test_recompute_businesses_can_be_pointed_at_one_business(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    business = seed_oak_hill(db)
    other = Business(display_name="Untouched Plumbing", website="https://stale.invalid/")
    db.add(other)
    db.commit()

    assert cli.main(["recompute-businesses", "--business-id", str(business.id)]) == 0

    db.expire_all()
    db.refresh(business)
    db.refresh(other)
    assert business.website_kind is WebsiteKind.own_site
    assert other.website == "https://stale.invalid/", "a business not named is not touched"
    printed = capsys.readouterr().out
    assert "Recomputed 1 business(es)" in printed


def test_recompute_businesses_rejects_a_business_id_that_is_not_a_uuid(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["recompute-businesses", "--business-id", "not-a-uuid"]) == 2
    assert "not a valid business id" in capsys.readouterr().out


def test_recompute_businesses_says_so_when_there_is_nothing_to_do(
    db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["recompute-businesses"]) == 0
    assert "no businesses to recompute" in capsys.readouterr().out
