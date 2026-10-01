"""RDAP domain lookups over HTTPS (spec v0.13.0, task 3).

Four facts are kept from a registry's answer and nothing else: the registrar's name, the
creation date, the expiry date and the status codes. RDAP puts people inside `entities` —
registrant, admin and tech contacts as vCards, and an abuse contact nested under the
registrar — so `parse_domain` is a parse boundary: it reads the `fn` (name) of the entity
whose roles include `registrar` and nothing else from any entity, and the value it returns
holds no reference to the response (decision C3). Nothing about a person ever reaches a
return value, a log line, an error or the database.

Endpoints come from a checked-in snapshot of the IANA bootstrap file
(`data/rdap_dns_bootstrap.json`, like the bundled Public Suffix List), and the registry is
called directly — never a third-party redirector (decision C14). A TLD the snapshot does not
list, an error, or an answer with no expiry date is `null`, and `null` produces nothing.

Calls go through `ApiHttpClient`, so the `rdap` token bucket and daily cap apply exactly as
they do to PageSpeed, and every call is metered in `api_calls` under the `rdap` source row.
One attempt, ten-second read: the whole DNS + RDAP step has fifteen seconds per business
(decision C15).
"""

import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Protocol

import httpx
from redis import Redis

from app.core.config import Settings, get_settings
from app.core.http import ApiHttpClient, MeteringHook
from app.core.logging import get_logger
from app.core.ratelimit import build_limiter
from app.modules.adapters.errors import AdapterError

logger = get_logger("app.rdap")

RDAP_SOURCE_NAME = "rdap"
RDAP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
RDAP_MAX_ATTEMPTS = 1
BOOTSTRAP_PATH = os.path.join(os.path.dirname(__file__), "data", "rdap_dns_bootstrap.json")


@dataclass(frozen=True)
class RdapFacts:
    """Everything kept from one RDAP answer. There is no field for anything else."""

    registrar: str | None
    created: datetime | None
    expires: datetime | None
    status: tuple[str, ...]


@dataclass(frozen=True)
class RdapResult:
    """A lookup's outcome: facts and the URL asked, or `None` and why."""

    facts: RdapFacts | None
    url: str | None
    reason: str | None = None

    def snapshot(self, *, checked_at: datetime) -> dict[str, Any] | None:
        """The stored form. `None` when there is nothing certain to store."""
        if self.facts is None:
            return None
        return {
            "source": RDAP_SOURCE_NAME,
            "source_url": self.url,
            "bootstrap_publication": bootstrap().publication,
            "checked_at": checked_at.isoformat(),
            "registrar": self.facts.registrar,
            "created": _iso(self.facts.created),
            "expires": _iso(self.facts.expires),
            "status": list(self.facts.status),
        }


class RdapClient(Protocol):
    def lookup(self, domain: str) -> RdapResult: ...


# --- the parse boundary ------------------------------------------------------------------


def parse_domain(body: Any) -> RdapFacts | None:
    """Read the four facts out of an RDAP domain answer. `None` without an expiry date.

    Never raises on a strange shape: an exception here would carry the body — contacts
    and all — into an error's details.
    """
    if not isinstance(body, Mapping):
        return None
    events = body.get("events")
    created = expires = None
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, Mapping):
            continue
        action = event.get("eventAction")
        if action == "registration" and created is None:
            created = _date(event.get("eventDate"))
        elif action == "expiration" and expires is None:
            expires = _date(event.get("eventDate"))
    if expires is None:
        return None
    raw_status = body.get("status")
    status = tuple(
        item
        for item in (raw_status if isinstance(raw_status, list) else [])
        if isinstance(item, str)
    )
    return RdapFacts(
        registrar=_registrar_name(body.get("entities")),
        created=created,
        expires=expires,
        status=status,
    )


def _registrar_name(entities: Any) -> str | None:
    """The `fn` of the top-level entity whose roles include `registrar`. Nothing else is read."""
    for entity in entities if isinstance(entities, list) else []:
        if not isinstance(entity, Mapping):
            continue
        roles = entity.get("roles")
        if not isinstance(roles, list) or "registrar" not in roles:
            continue
        vcard = entity.get("vcardArray")
        if not isinstance(vcard, list) or len(vcard) < 2 or not isinstance(vcard[1], list):
            return None
        for prop in vcard[1]:
            if isinstance(prop, list) and len(prop) >= 4 and prop[0] == "fn":
                value = prop[3]
                return value.strip() or None if isinstance(value, str) else None
        return None
    return None


def _date(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


# --- endpoints -------------------------------------------------------------------------


@dataclass(frozen=True)
class Bootstrap:
    publication: str | None
    services: Mapping[str, str]

    def base_url(self, domain: str) -> str | None:
        """The HTTPS base URL for `domain`'s TLD, longest matching label suffix first."""
        labels = domain.lower().rstrip(".").split(".")
        for start in range(1, len(labels)):
            url = self.services.get(".".join(labels[start:]))
            if url is not None:
                return url
        return None


@lru_cache
def bootstrap(path: str = BOOTSTRAP_PATH) -> Bootstrap:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    services: dict[str, str] = {}
    for tlds, urls in data.get("services", []):
        https = [url for url in urls if url.lower().startswith("https://")]
        if not https:
            continue
        base = https[0] if https[0].endswith("/") else f"{https[0]}/"
        for tld in tlds:
            services[tld.lower()] = base
    return Bootstrap(publication=data.get("publication"), services=services)


# --- clients ---------------------------------------------------------------------------


class NetworkRdapClient:
    def __init__(
        self, http: ApiHttpClient, *, user_agent: str, endpoints: Bootstrap | None = None
    ) -> None:
        self._http = http
        self._user_agent = user_agent
        self._endpoints = endpoints or bootstrap()

    def lookup(self, domain: str) -> RdapResult:
        base = self._endpoints.base_url(domain)
        if base is None:
            return RdapResult(None, None, "no RDAP endpoint for this TLD")
        url = f"{base}domain/{domain}"
        try:
            facts = self._http.request_json(
                "GET",
                url,
                parse=parse_domain,
                headers={"Accept": "application/rdap+json", "User-Agent": self._user_agent},
            )
        except AdapterError as exc:
            # The class only: an error's details can carry the response body.
            logger.info(
                "rdap lookup failed", extra={"domain": domain, "error_class": type(exc).__name__}
            )
            return RdapResult(None, url, f"RDAP lookup failed ({type(exc).__name__})")
        if facts is None:
            return RdapResult(None, url, "RDAP answer had no expiry date")
        return RdapResult(facts, url)


class OfflineRdapClient:
    """Development and CI: no live registry is ever asked (decision C13)."""

    def lookup(self, domain: str) -> RdapResult:
        return RdapResult(None, None, "no live RDAP in this environment")


def build_rdap_client(
    *,
    job_run_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    redis_client: Redis | None = None,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
    meter: MeteringHook | None = None,
) -> RdapClient:
    """The production wiring: metered in `api_calls`, rate limited and daily capped under
    the `rdap` source key, exactly like PageSpeed."""
    config = settings or get_settings()
    if config.fixtures_allowed:
        return OfflineRdapClient()
    from app.core.redis import get_redis

    if meter is None:
        from app.modules.discovery.service import api_call_meter

        meter = api_call_meter(job_run_id)
    return network_rdap_client(
        config, redis_client or get_redis(), clock=clock, sleeper=sleeper, meter=meter
    )


def network_rdap_client(
    config: Settings,
    redis_client: Redis,
    *,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
    meter: MeteringHook | None = None,
) -> NetworkRdapClient:
    http = ApiHttpClient(
        source=RDAP_SOURCE_NAME,
        meter=meter,
        limiter=build_limiter(
            redis_client,
            source=RDAP_SOURCE_NAME,
            requests_per_second=config.rdap_rps,
            burst=max(int(config.rdap_rps), 1),
            daily_call_cap=config.rdap_daily_call_cap,
            clock=clock,
            sleeper=sleeper,
        ),
        sleeper=sleeper,
        max_attempts=RDAP_MAX_ATTEMPTS,
        timeout=RDAP_TIMEOUT,
    )
    return NetworkRdapClient(http, user_agent=config.user_agent)


def rdap_source_config(settings: Settings | None = None) -> dict[str, Any]:
    """The `sources` row config: the same shape PageSpeed's has, and the same role."""
    from app.modules.audit_web.psi import AUDIT_SERVICE_ROLE

    config = settings or get_settings()
    return {
        "role": AUDIT_SERVICE_ROLE,
        "display_name": "RDAP (domain registry)",
        "terms_url": "https://www.iana.org/domains/rdap",
        "commercial_use_note": (
            "Public registry lookups, called directly at each registry. Only the registrar's "
            "name, creation date, expiry date and status codes are kept; every contact is "
            "dropped at the parse boundary."
        ),
        # Registry facts about a domain, not licensed provider content.
        "content_ttl_days": 0,
        "exclude_from_crm_export": False,
        "rate_limit": {
            "requests_per_second": config.rdap_rps,
            "burst": max(int(config.rdap_rps), 1),
            "daily_call_cap": config.rdap_daily_call_cap,
        },
    }


def rdap_service_source() -> Any:
    from app.modules.audit_web.psi import ServiceSourceSpec
    from app.modules.sources.models import SourceKind

    return ServiceSourceSpec(
        name=RDAP_SOURCE_NAME, kind=SourceKind.api, config=rdap_source_config()
    )
