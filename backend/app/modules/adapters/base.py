"""The contract every source must satisfy (blueprint slide 18).

A source is reachable through exactly seven verbs, so the discovery worker never needs to
know which provider it is talking to. Everything an adapter returns is copied as it was
received: mapping is allowed, cleaning and guessing are not — unknown stays `None`, and
v0.3.0 is what turns these raw candidates into businesses.
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.modules.jobs.schemas import GeoSpec
from app.modules.sources.models import SourceKind

# A search area, in the same shape a search job stores it.
Geo = GeoSpec


def _no_cancel() -> None:
    """Default cancel check: never interrupts. The worker passes a real one."""


@dataclass(frozen=True)
class DiscoveryConfig:
    """What one discovery pass should look for, and how it can be stopped."""

    industry: str
    geo: Geo
    job_run_id: uuid.UUID
    max_results: int = 60
    # Raises to abort the pass; the worker wires this to the job-run checkpoint.
    cancel_check: Callable[[], None] = _no_cancel


@dataclass(frozen=True)
class Ref:
    """A pointer to one result. `payload` is set when discovery already carried the data."""

    source_record_id: str
    source_url: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class RawDoc:
    """Exactly what the source returned for one record, with its provenance."""

    source: str
    source_record_id: str
    source_url: str | None
    payload: dict[str, Any]
    fetched_at: datetime


@dataclass(frozen=True)
class AddressPart:
    """One structured piece of an address, in the shape Places returns them.

    Source-agnostic on purpose: a future adapter fills the same three fields, and
    normalization never learns which provider it is reading.
    """

    long_text: str | None = None
    short_text: str | None = None
    types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """A light mapping of a RawDoc. Every field is optional and is never guessed."""

    display_name: str | None = None
    formatted_address: str | None = None
    phone: str | None = None
    website: str | None = None
    business_status: str | None = None
    types: list[str] | None = None
    primary_type: str | None = None
    address_components: list[AddressPart] | None = None
    lat: float | None = None
    lng: float | None = None


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(valid=True)

    @classmethod
    def invalid(cls, *errors: str) -> "ValidationResult":
        return cls(valid=False, errors=list(errors))


@dataclass(frozen=True)
class Event:
    """A signal derived from a record (hiring, funding, …). Unused until v0.5.0."""

    kind: str
    payload: dict[str, Any]
    observed_at: datetime | None = None


@dataclass(frozen=True)
class RateLimit:
    requests_per_second: float
    burst: int
    daily_call_cap: int


@dataclass(frozen=True)
class SourceMeta:
    """What an operator needs to know before enabling a source."""

    display_name: str
    terms_url: str
    commercial_use_note: str
    content_ttl_days: int

    def as_config(self, rate_limit: RateLimit) -> dict[str, Any]:
        """The JSON stored on the `sources` row."""
        return {
            "display_name": self.display_name,
            "terms_url": self.terms_url,
            "commercial_use_note": self.commercial_use_note,
            "content_ttl_days": self.content_ttl_days,
            "rate_limit": {
                "requests_per_second": rate_limit.requests_per_second,
                "burst": rate_limit.burst,
                "daily_call_cap": rate_limit.daily_call_cap,
            },
        }


@runtime_checkable
class SourceAdapter(Protocol):
    """Everything the discovery worker is allowed to know about a source."""

    name: str
    kind: SourceKind

    def discover(self, cfg: DiscoveryConfig) -> Iterator[Ref]: ...

    def fetch(self, ref: Ref) -> RawDoc: ...

    def validate(self, raw: RawDoc) -> ValidationResult: ...

    def normalize(self, raw: RawDoc) -> Candidate: ...

    def emit_events(self, raw: RawDoc) -> list[Event]: ...

    def get_rate_limit(self) -> RateLimit: ...

    def get_source_metadata(self) -> SourceMeta: ...
