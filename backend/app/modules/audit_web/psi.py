"""PageSpeed Insights: the one measurement in an audit we do not make ourselves.

It goes through `core/http.py` like every other official API, which is what makes its
calls metered in `api_calls`, rate limited and capped per day — PSI is free but not
unlimited, and a run that audits a thousand businesses must not be able to spend the
quota of the whole organisation in a minute.

A PSI failure never fails an audit. The rest of the homepage audit is deterministic and
ours; the score is extra, so when PSI is unavailable the audit finishes with `psi = null`
and `checks.psi_error` saying why.
"""

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from redis import Redis

from app.core.config import Settings, get_settings
from app.core.fetch_backends import RESERVED_SUFFIX, demo_site_dir
from app.core.http import ApiHttpClient
from app.core.logging import get_logger
from app.core.ratelimit import build_limiter
from app.modules.adapters.base import RateLimit
from app.modules.adapters.errors import AdapterError
from app.modules.sources.models import SourceKind

logger = get_logger("app.audit_web.psi")

PAGESPEED_SOURCE_NAME = "pagespeed_insights"
# Marks a `sources` row the pipeline calls for a service rather than to find businesses.
# `validate_source_ids` refuses such a row for a search job.
AUDIT_SERVICE_ROLE = "audit_service"
PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
PSI_STRATEGY = "mobile"
PSI_FIXTURE_FILENAME = "psi.json"
# The Lighthouse categories one request asks for. PSI returns only the ones requested.
PSI_CATEGORIES = ("performance", "accessibility", "best-practices")
NO_KEY_REASON = "No PAGESPEED_API_KEY is configured, so PageSpeed Insights was not called"
CONTENT_TTL_DAYS_UNLIMITED = 0


@dataclass(frozen=True)
class PsiResult:
    """The mobile numbers an audit keeps. Anything PSI did not report stays `None`."""

    performance_score: int | None = None
    accessibility_score: int | None = None
    best_practices_score: int | None = None
    lcp_ms: int | None = None
    cls: float | None = None
    tbt_ms: int | None = None
    crux_category: str | None = None
    strategy: str = PSI_STRATEGY

    def as_dict(self) -> dict[str, Any]:
        return {
            "performance_score": self.performance_score,
            "accessibility_score": self.accessibility_score,
            "best_practices_score": self.best_practices_score,
            "lcp_ms": self.lcp_ms,
            "cls": self.cls,
            "tbt_ms": self.tbt_ms,
            "crux_category": self.crux_category,
            "strategy": self.strategy,
        }


class PageSpeedUnavailableError(Exception):
    """PSI did not answer usefully. The reason is stored on the audit, not raised further."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PageSpeedClient(Protocol):
    def analyse(self, url: str) -> PsiResult: ...


def parse_psi(payload: Any) -> PsiResult:
    """Read the fields we keep out of a Lighthouse response, tolerating missing ones."""
    if not isinstance(payload, dict):
        raise PageSpeedUnavailableError("PageSpeed answered something that is not an object")
    if "error" in payload:
        raise PageSpeedUnavailableError(_error_message(payload["error"]))

    lighthouse = payload.get("lighthouseResult")
    lighthouse = lighthouse if isinstance(lighthouse, dict) else {}
    categories = _dict(lighthouse.get("categories"))
    audits = _dict(lighthouse.get("audits"))

    return PsiResult(
        performance_score=_category_score(categories, "performance"),
        accessibility_score=_category_score(categories, "accessibility"),
        best_practices_score=_category_score(categories, "best-practices"),
        lcp_ms=_numeric_int(audits, "largest-contentful-paint"),
        cls=_numeric_float(audits, "cumulative-layout-shift"),
        tbt_ms=_numeric_int(audits, "total-blocking-time"),
        crux_category=_crux_category(payload),
        strategy=str(payload.get("strategy") or PSI_STRATEGY),
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _category_score(categories: dict[str, Any], key: str) -> int | None:
    """A Lighthouse category score (0 to 1) as a whole number out of 100, or `None`.

    `None` when the category is absent *or* its score is null — Lighthouse reports a null
    score when a category could not be computed, and that is not a zero.
    """
    raw = _dict(categories.get(key)).get("score")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return round(float(raw) * 100)


def _numeric_int(audits: dict[str, Any], key: str) -> int | None:
    value = _dict(audits.get(key)).get("numericValue")
    return round(float(value)) if isinstance(value, int | float) else None


def _numeric_float(audits: dict[str, Any], key: str) -> float | None:
    value = _dict(audits.get(key)).get("numericValue")
    return round(float(value), 3) if isinstance(value, int | float) else None


def _crux_category(payload: dict[str, Any]) -> str | None:
    """The field data category, present only for sites with enough real-user traffic."""
    experience = _dict(payload.get("loadingExperience"))
    category = experience.get("overall_category")
    return str(category) if isinstance(category, str) else None


def _error_message(error: object) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "PageSpeed reported an error with no message"


class NetworkPageSpeedClient:
    """The real client. One GET per audit, through the shared retrying HTTP client."""

    def __init__(
        self,
        http: ApiHttpClient,
        *,
        api_key: str = "",
        endpoint: str = PSI_ENDPOINT,
        strategy: str = PSI_STRATEGY,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._endpoint = endpoint
        self._strategy = strategy

    def close(self) -> None:
        self._http.close()

    def analyse(self, url: str) -> PsiResult:
        # Without a key PSI answers from a small quota shared by every keyless caller, so
        # a score would appear on some audits and not others for reasons nobody can see.
        # v0.11.0: no key means no call, and every score is null with the reason recorded.
        if not self._api_key:
            raise PageSpeedUnavailableError(NO_KEY_REASON)
        query: dict[str, str | list[str]] = {
            "url": url,
            "strategy": self._strategy,
            "category": list(PSI_CATEGORIES),
            # PSI takes its key as a query parameter; the client's secret list keeps it
            # out of every log line and error body.
            "key": self._api_key,
        }
        target = f"{self._endpoint}?{_encode(query)}"
        try:
            return self._http.request_json("GET", target, parse=parse_psi)
        except AdapterError as exc:
            # Quota, auth, 5xx and unparseable bodies all land here. None of them is
            # allowed to fail the audit around them.
            raise PageSpeedUnavailableError(f"{type(exc).__name__}: {exc}") from exc


def _encode(query: dict[str, str | list[str]]) -> str:
    from urllib.parse import urlencode

    return urlencode(query, doseq=True)


class FixturePageSpeedClient:
    """Answers from `app/demo/sites/<host>/psi.json`. Development and tests only.

    Same rule as the fixture fetch backend: a demo host is answered from disk, so a demo
    load and the whole test suite need neither a key nor a network. A host it does not
    know is handed to the real client.
    """

    def __init__(self, fallback: PageSpeedClient | None = None, root: str | None = None) -> None:
        self._fallback = fallback
        self._root = root

    def analyse(self, url: str) -> PsiResult:
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").lower()
        directory = demo_site_dir(host, root=self._root) if host else None
        if directory is None:
            if host.endswith(RESERVED_SUFFIX) or self._fallback is None:
                raise PageSpeedUnavailableError(
                    f"no PageSpeed fixture for '{host}' and no live client in this environment"
                )
            return self._fallback.analyse(url)

        path = os.path.join(directory, PSI_FIXTURE_FILENAME)
        if not os.path.isfile(path):
            raise PageSpeedUnavailableError(f"no {PSI_FIXTURE_FILENAME} for '{host}'")
        with open(path, encoding="utf-8") as handle:
            return parse_psi(json.load(handle))


def pagespeed_source_config(settings: Settings | None = None) -> dict[str, Any]:
    """The `sources` row config. Same shape an adapter's row has, plus its role."""
    config = settings or get_settings()
    return {
        "role": AUDIT_SERVICE_ROLE,
        "display_name": "PageSpeed Insights",
        "terms_url": "https://developers.google.com/speed/docs/insights/v5/about",
        "commercial_use_note": (
            "Free Google API. A key raises the quota; without one the shared quota is low. "
            "Only scores and timings are stored, never page content."
        ),
        # PSI returns measurements of a public page, not licensed provider content, so
        # nothing here expires on a provider's clock.
        "content_ttl_days": CONTENT_TTL_DAYS_UNLIMITED,
        "exclude_from_crm_export": False,
        "rate_limit": {
            "requests_per_second": config.psi_rps,
            "burst": max(int(config.psi_rps), 1),
            "daily_call_cap": config.psi_daily_call_cap,
        },
    }


@dataclass(frozen=True)
class ServiceSourceSpec:
    """A `sources` row for an API the pipeline calls for a service, not for discovery."""

    name: str
    kind: SourceKind
    config: dict[str, Any]


def pagespeed_service_source() -> ServiceSourceSpec:
    return ServiceSourceSpec(
        name=PAGESPEED_SOURCE_NAME, kind=SourceKind.api, config=pagespeed_source_config()
    )


def build_psi_client(
    *,
    job_run_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    redis_client: Redis | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    meter: Any = None,
    endpoint: str = PSI_ENDPOINT,
) -> PageSpeedClient:
    """The production wiring: metered, rate limited, fixtures first in development."""
    from app.core.redis import get_redis

    config = settings or get_settings()
    api_key = config.pagespeed_api_key.get_secret_value()
    if meter is None:
        from app.modules.discovery.service import api_call_meter

        meter = api_call_meter(job_run_id)

    limits = RateLimit(
        requests_per_second=config.psi_rps,
        burst=max(int(config.psi_rps), 1),
        daily_call_cap=config.psi_daily_call_cap,
    )
    http = ApiHttpClient(
        source=PAGESPEED_SOURCE_NAME,
        meter=meter,
        limiter=build_limiter(
            redis_client or get_redis(),
            source=PAGESPEED_SOURCE_NAME,
            requests_per_second=limits.requests_per_second,
            burst=limits.burst,
            daily_call_cap=limits.daily_call_cap,
            clock=clock,
            sleeper=sleeper,
        ),
        sleeper=sleeper,
        secrets=[api_key] if api_key else [],
    )
    network = NetworkPageSpeedClient(http, api_key=api_key, endpoint=endpoint)
    if config.fixtures_allowed:
        return FixturePageSpeedClient(fallback=network)
    return network
