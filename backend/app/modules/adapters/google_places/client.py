"""A thin client for `places:searchText` — the only Places endpoint this build calls.

Place Details is deliberately absent: Text Search already returns every field in the
default mask, and a Details call would be a second billable request per record.
"""

import time
import uuid
from collections.abc import Callable
from typing import Any

from redis import Redis

from app.core.config import get_settings
from app.core.http import ApiHttpClient
from app.core.ratelimit import build_limiter
from app.core.redis import get_redis
from app.modules.adapters.base import RateLimit
from app.modules.adapters.google_places.schemas import TextSearchPage

PLACES_BASE_URL = "https://places.googleapis.com"
TEXT_SEARCH_PATH = "/v1/places:searchText"
# The API's maximum, and always what discovery asks for: asking for fewer near the limit
# made Places send short pages and then empty ones that still carried a token.
PAGE_SIZE = 20


class PlacesTextSearchClient:
    """Wraps one `ApiHttpClient` with the Places headers and response parsing."""

    def __init__(
        self,
        http: ApiHttpClient,
        *,
        api_key: str,
        field_mask: str,
        base_url: str = PLACES_BASE_URL,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._field_mask = field_mask
        self._url = f"{base_url.rstrip('/')}{TEXT_SEARCH_PATH}"

    def close(self) -> None:
        self._http.close()

    def search_text(
        self,
        text_query: str,
        *,
        page_token: str | None = None,
        location_bias: dict[str, Any] | None = None,
        page_size: int = PAGE_SIZE,
    ) -> TextSearchPage:
        body: dict[str, Any] = {"textQuery": text_query, "pageSize": page_size}
        if page_token:
            body["pageToken"] = page_token
        if location_bias:
            body["locationBias"] = location_bias
        return self._http.request_json(
            "POST",
            self._url,
            headers={
                # The key travels in a header, never in the URL, so it cannot leak through
                # a log line, a redirect or an error that echoes the request target.
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": self._field_mask,
                "Content-Type": "application/json",
            },
            json=body,
            parse=TextSearchPage.model_validate,
        )


def build_client(
    source: str,
    *,
    job_run_id: uuid.UUID | None = None,
    max_calls: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    redis_client: Redis | None = None,
    rate_limit: RateLimit | None = None,
    meter: Any = None,
    base_url: str = PLACES_BASE_URL,
) -> PlacesTextSearchClient:
    """The production wiring: settings, the shared Redis limiter and the DB metering hook.

    `max_calls` is the run's safety ceiling (`adapter.run_call_ceiling`): the client refuses
    any call past it, counted per job run. Left unset, only the daily cap applies.
    """
    settings = get_settings()
    api_key = settings.google_places_api_key.get_secret_value()
    limits = rate_limit or RateLimit(
        requests_per_second=settings.places_rps,
        burst=max(int(settings.places_rps), 1),
        daily_call_cap=settings.places_daily_call_cap,
    )
    if meter is None:
        # Imported here: the discovery module imports adapters, so a module-level import
        # would close the loop.
        from app.modules.discovery.service import api_call_meter

        meter = api_call_meter(job_run_id)

    http = ApiHttpClient(
        source=source,
        meter=meter,
        limiter=build_limiter(
            redis_client or get_redis(),
            source=source,
            requests_per_second=limits.requests_per_second,
            burst=limits.burst,
            daily_call_cap=limits.daily_call_cap,
            run_call_cap=max_calls,
            run_call_cap_setting="PLACES_RUN_CALL_CAP_MULTIPLIER",
            job_run_id=job_run_id,
            clock=clock,
            sleeper=sleeper,
        ),
        sleeper=sleeper,
        secrets=[api_key] if api_key else [],
    )
    return PlacesTextSearchClient(
        http, api_key=api_key, field_mask=settings.places_field_mask, base_url=base_url
    )
