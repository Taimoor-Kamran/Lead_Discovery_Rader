"""A source made of a checked-in file, so the pipeline can be exercised without a key.

Registered only on a developer's machine (`APP_ENV=development`, which `local` counts
as). Its payloads use the Places response shape, so it exercises exactly the same
normalization path as the real adapter — the point is to test resolution, not to invent
a second mapping.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.modules.adapters.base import (
    Candidate,
    DiscoveryConfig,
    Event,
    RateLimit,
    RawDoc,
    Ref,
    SourceMeta,
    ValidationResult,
)
from app.modules.adapters.errors import AdapterError
from app.modules.adapters.google_places.adapter import GooglePlacesAdapter
from app.modules.sources.models import SourceKind

SOURCE_NAME = "demo_fixture"
FIXTURE_PATH = Path(__file__).resolve().parents[2] / "demo" / "austin_plumbers.json"
COMMERCIAL_USE_NOTE = (
    "Fictional data checked into the repository for local testing. It is not a provider, "
    "it never expires, and it must never be exported to a CRM."
)


@lru_cache(maxsize=1)
def load_fixture(path: str | None = None) -> dict[str, Any]:
    """Read the demo file once. It is a static asset, not a response."""
    target = Path(path) if path else FIXTURE_PATH
    with target.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def demo_places(path: str | None = None) -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = list(load_fixture(path).get("places", []))
    return places


class DemoFixtureAdapter:
    """`SourceAdapter` over the checked-in fixture. Makes no network call, ever."""

    name = SOURCE_NAME
    kind = SourceKind.feed

    def __init__(self, fixture_path: str | None = None) -> None:
        self._fixture_path = fixture_path
        # The mapping is the Places one: the fixture is written in that shape.
        self._mapper = GooglePlacesAdapter()

    def discover(self, cfg: DiscoveryConfig) -> Iterator[Ref]:
        for index, payload in enumerate(demo_places(self._fixture_path)):
            if index >= cfg.max_results:
                return
            cfg.cancel_check()
            yield Ref(source_record_id=str(payload.get("id", "")), payload=payload)

    def fetch(self, ref: Ref) -> RawDoc:
        if ref.payload is None:
            raise AdapterError(
                "Demo refs always carry their payload; nothing to fetch",
                details={"source_record_id": ref.source_record_id},
                source=SOURCE_NAME,
            )
        return RawDoc(
            source=SOURCE_NAME,
            source_record_id=ref.source_record_id,
            source_url=None,
            payload=ref.payload,
            fetched_at=datetime.now(UTC),
        )

    def validate(self, raw: RawDoc) -> ValidationResult:
        return self._mapper.validate(raw)

    def normalize(self, raw: RawDoc) -> Candidate:
        return self._mapper.normalize(raw)

    def emit_events(self, raw: RawDoc) -> list[Event]:
        return []

    def get_rate_limit(self) -> RateLimit:
        """Nothing is fetched, so nothing needs limiting — but the contract asks."""
        return RateLimit(requests_per_second=1000.0, burst=1000, daily_call_cap=0)

    def get_source_metadata(self) -> SourceMeta:
        return SourceMeta(
            display_name="Demo fixture (Austin plumbers)",
            terms_url="",
            commercial_use_note=COMMERCIAL_USE_NOTE,
            # Fictional data has no provider retention window to honour.
            content_ttl_days=0,
            exclude_from_crm_export=True,
        )


def new_run_id() -> uuid.UUID:
    return uuid.uuid4()
