"""The Google Places adapter: turn an industry + area into raw place records.

Only Text Search (New) is used. Each result already carries every field in the mask, so
`fetch()` is a pure wrapper over what discovery has seen — there is no second call and no
extra cost per record.
"""

import math
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.adapters.base import (
    AddressPart,
    Candidate,
    DiscoveryConfig,
    Event,
    Geo,
    RateLimit,
    RawDoc,
    Ref,
    SourceMeta,
    ValidationResult,
)
from app.modules.adapters.errors import AdapterError
from app.modules.adapters.google_places.client import (
    PAGE_SIZE,
    PlacesTextSearchClient,
    build_client,
)
from app.modules.adapters.google_places.schemas import Place
from app.modules.sources.models import SourceKind

logger = get_logger("app.adapters.google_places")

SOURCE_NAME = "google_places"
TERMS_URL = "https://cloud.google.com/maps-platform/terms"
COMMERCIAL_USE_NOTE = (
    "Google Maps Platform terms allow caching place IDs indefinitely but limit how long "
    "other Places content may be stored. PLACES_CONTENT_TTL_DAYS drives `purge-expired`, "
    "which nulls the stored payload and keeps only the place ID."
)
# Text Search (New) returns up to 20 places a page and at most 60 per query — but pages
# often come back short, so 60 results is not 3 calls. Two live walks of "electrician in
# Austin, TX" on 2026-09-24: asking for 20 every time took 5 calls for 60 places (20, 16, 12,
# 10, 2, then no token); asking for only what was still wanted took 6 calls for 52 (19, 16,
# 12, 4, 1, 0) and the empty last page still carried a token. The count per page also varies
# between identical requests (page 1 was 20 on one walk, 19 on the next).
MAX_RESULTS_PER_QUERY = 60

# (job_run_id, the run's call ceiling) -> a client that refuses any call past the ceiling.
ClientFactory = Callable[[uuid.UUID | None, int], PlacesTextSearchClient]


class GooglePlacesAdapter:
    """`SourceAdapter` for Places Text Search (New)."""

    name = SOURCE_NAME
    kind = SourceKind.api

    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client_factory = client_factory or (
            lambda job_run_id, max_calls: build_client(
                SOURCE_NAME,
                job_run_id=job_run_id,
                max_calls=max_calls,
                sleeper=sleeper,
                clock=clock,
            )
        )

    # --- contract ---------------------------------------------------------------

    def discover(self, cfg: DiscoveryConfig) -> Iterator[Ref]:
        """Page through Text Search until the limit, the last page, or a page with nothing new.

        Every page asks for a full 20 and the last is trimmed here. Asking for fewer near the
        limit is what made Places answer with short pages and then empty ones (see
        `MAX_RESULTS_PER_QUERY`), and it saves nothing: Places bills per request.
        """
        limit = effective_limit(cfg.max_results)
        if limit <= 0:
            return

        text_query, location_bias = build_query(cfg.industry, cfg.geo)
        client = self._client_factory(cfg.job_run_id, run_call_ceiling(limit))
        seen: set[str] = set()
        yielded = 0
        pages = 0
        page_token: str | None = None
        try:
            while yielded < limit:
                cfg.cancel_check()
                page = client.search_text(
                    text_query, page_token=page_token, location_bias=location_bias
                )
                pages += 1
                fresh = 0
                for payload in page.places:
                    if yielded >= limit:
                        break
                    place_id = payload.get("id")
                    if isinstance(place_id, str) and place_id:
                        if place_id in seen:
                            continue  # the same place again: nothing to store, no progress
                        seen.add(place_id)
                        fresh += 1
                    yield _ref_from_payload(payload)
                    yielded += 1
                page_token = page.next_page_token
                if not page_token:
                    break
                if fresh == 0:
                    # Places will go on answering 200 with no places, or none it has not
                    # already sent, and a fresh token. Following it never ends: every call is
                    # billed and nothing new arrives. This cost 500 calls before v0.11.0.
                    logger.warning(
                        "a Places page brought no new place but still carried a token; stopping",
                        extra={
                            "job_run_id": str(cfg.job_run_id),
                            "page": pages,
                            "places_on_page": len(page.places),
                            "yielded": yielded,
                        },
                    )
                    break
        finally:
            client.close()

    def fetch(self, ref: Ref) -> RawDoc:
        """Text Search already returned everything; wrap it rather than call again."""
        if ref.payload is None:
            raise AdapterError(
                "Google Places refs always carry their payload; nothing to fetch",
                details={"source_record_id": ref.source_record_id},
                source=SOURCE_NAME,
            )
        return RawDoc(
            source=SOURCE_NAME,
            source_record_id=ref.source_record_id,
            source_url=ref.source_url,
            payload=ref.payload,
            fetched_at=datetime.now(UTC),
        )

    def validate(self, raw: RawDoc) -> ValidationResult:
        try:
            Place.model_validate(raw.payload)
        except ValidationError as exc:
            return ValidationResult.invalid(
                *(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
            )
        return ValidationResult.ok()

    def normalize(self, raw: RawDoc) -> Candidate:
        """Copy the fields we use. Anything the payload omits stays `None` — never guessed."""
        place = Place.model_validate(raw.payload)
        return Candidate(
            display_name=place.display_name.text if place.display_name else None,
            formatted_address=place.formatted_address,
            phone=place.national_phone_number or place.international_phone_number,
            website=place.website_uri,
            business_status=place.business_status,
            types=list(place.types) if place.types else None,
            primary_type=place.primary_type,
            address_components=(
                [
                    AddressPart(
                        long_text=part.long_text,
                        short_text=part.short_text,
                        types=tuple(part.types),
                    )
                    for part in place.address_components
                ]
                if place.address_components
                else None
            ),
            lat=place.location.latitude if place.location else None,
            lng=place.location.longitude if place.location else None,
            rating=place.rating,
            user_rating_count=place.user_rating_count,
        )

    def emit_events(self, raw: RawDoc) -> list[Event]:
        """Places carries no signals worth an event in the MVP."""
        return []

    def get_rate_limit(self) -> RateLimit:
        settings = get_settings()
        return RateLimit(
            requests_per_second=settings.places_rps,
            burst=max(int(settings.places_rps), 1),
            daily_call_cap=settings.places_daily_call_cap,
        )

    def get_source_metadata(self) -> SourceMeta:
        return SourceMeta(
            display_name="Google Places API (New)",
            terms_url=TERMS_URL,
            commercial_use_note=COMMERCIAL_USE_NOTE,
            content_ttl_days=get_settings().places_content_ttl_days,
        )


def effective_limit(requested: int) -> int:
    """Never exceed the operator's per-job cap or the API's own 60-result ceiling."""
    hard_cap = min(get_settings().places_max_results_per_job, MAX_RESULTS_PER_QUERY)
    return max(min(requested, hard_cap), 0)


def run_call_ceiling(limit: int) -> int:
    """The most Places calls one run for `limit` results may make: a safety limit, enforced.

    Its base is the fewest pages `limit` results could possibly take, ceil(limit / 20). That
    is a floor, not an estimate — pages come back short, so real runs take more (see
    `MAX_RESULTS_PER_QUERY`). `PLACES_RUN_CALL_CAP_MULTIPLIER` times that floor leaves room
    for short pages and retries; a run that still reaches it is looping. The cost estimate
    shows this same number, so what a client is told is what the system enforces.
    """
    if limit <= 0:
        return 0
    return get_settings().places_run_call_cap_multiplier * math.ceil(limit / PAGE_SIZE)


def build_query(industry: str, geo: Geo) -> tuple[str, dict[str, Any] | None]:
    """Turn an industry plus either geo shape into a `textQuery` and an optional bias."""
    if geo.city and geo.state:
        return f"{industry} in {geo.city}, {geo.state}", None
    if geo.lat is not None and geo.lng is not None and geo.radius_m is not None:
        bias = {
            "circle": {
                "center": {"latitude": geo.lat, "longitude": geo.lng},
                "radius": float(geo.radius_m),
            }
        }
        return industry, bias
    raise AdapterError(
        "A search area needs either a city and state, or a lat, lng and radius",
        details={"geo": geo.model_dump(exclude_none=True)},
        source=SOURCE_NAME,
    )


def _ref_from_payload(payload: dict[str, Any]) -> Ref:
    """Build a Ref without judging the payload; `validate()` decides what is usable."""
    place_id = payload.get("id")
    identifier = place_id if isinstance(place_id, str) else ""
    return Ref(
        source_record_id=identifier,
        source_url=(
            f"https://www.google.com/maps/place/?q=place_id:{identifier}" if identifier else None
        ),
        payload=payload,
    )
