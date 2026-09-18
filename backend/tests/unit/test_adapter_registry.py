"""The adapter registry: one name, one adapter, and one `sources` row per adapter."""

from collections.abc import Iterator

import pytest

from app.modules.adapters import registry
from app.modules.adapters.base import (
    Candidate,
    DiscoveryConfig,
    Event,
    RateLimit,
    RawDoc,
    Ref,
    SourceAdapter,
    SourceMeta,
    ValidationResult,
)
from app.modules.adapters.errors import AdapterError
from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
from app.modules.adapters.registry import UnknownAdapterError
from app.modules.sources.models import SourceKind


class StubAdapter:
    """A minimal adapter, so registry behaviour is tested without any HTTP at all."""

    kind = SourceKind.feed

    def __init__(self, name: str = "stub", ttl_days: int = 7) -> None:
        self.name = name
        self._ttl_days = ttl_days

    def discover(self, cfg: DiscoveryConfig) -> Iterator[Ref]:
        yield Ref(source_record_id="one", payload={})

    def fetch(self, ref: Ref) -> RawDoc:
        raise NotImplementedError

    def validate(self, raw: RawDoc) -> ValidationResult:
        return ValidationResult.ok()

    def normalize(self, raw: RawDoc) -> Candidate:
        return Candidate()

    def emit_events(self, raw: RawDoc) -> list[Event]:
        return []

    def get_rate_limit(self) -> RateLimit:
        return RateLimit(requests_per_second=1.0, burst=2, daily_call_cap=10)

    def get_source_metadata(self) -> SourceMeta:
        return SourceMeta(
            display_name="Stub",
            terms_url="https://example.test/terms",
            commercial_use_note="internal test double",
            content_ttl_days=self._ttl_days,
        )


def test_the_google_places_adapter_registers_itself() -> None:
    assert GOOGLE_PLACES in registry.names()
    assert registry.get(GOOGLE_PLACES).kind is SourceKind.api


def test_a_stub_satisfies_the_protocol() -> None:
    assert isinstance(StubAdapter(), SourceAdapter)


def test_register_then_get_returns_the_same_object() -> None:
    adapter = StubAdapter()
    registry.register(adapter)

    assert registry.get("stub") is adapter
    assert "stub" in registry.names()


def test_a_duplicate_name_is_rejected() -> None:
    registry.register(StubAdapter())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(StubAdapter())


def test_a_duplicate_name_is_allowed_when_replacing_deliberately() -> None:
    registry.register(StubAdapter())
    replacement = StubAdapter()

    registry.register(replacement, replace=True)

    assert registry.get("stub") is replacement


def test_an_adapter_without_a_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty name"):
        registry.register(StubAdapter(name=""))


def test_an_unknown_name_raises_a_typed_error() -> None:
    with pytest.raises(UnknownAdapterError) as caught:
        registry.get("nope")

    assert isinstance(caught.value, AdapterError)
    assert caught.value.status_code == 404


def test_unregister_removes_an_adapter() -> None:
    registry.register(StubAdapter())

    assert registry.unregister("stub") is not None
    assert "stub" not in registry.names()


def test_all_returns_adapters_in_name_order() -> None:
    registry.register(StubAdapter(name="zzz"))
    registry.register(StubAdapter(name="aaa"))

    assert [a.name for a in registry.all()] == sorted(a.name for a in registry.all())
