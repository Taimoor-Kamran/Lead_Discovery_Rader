"""One audit's domain intelligence: choose the domain, reuse or refresh the cache, report.

Runs after the fetch outcome is known, whatever it was (spec v0.13.0, "Sequencing"): the
site host comes from `final_url`, and `domain_no_a_record` needs to know whether the site
gave any HTTP answer at all. DNS and RDAP are asked side by side, and the two together get
`BUDGET_SECONDS` per business (decision C15): whatever has not answered by then is unknown,
and unknown produces nothing.

The result goes into the audit's `checks.domain_intel` in full — cached or not — so the
audit row alone reproduces its domain findings.
"""

import os
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.dns import FixtureResolver, LiveResolver, Rcode, Resolver
from app.core.logging import get_logger
from app.core.rdap import RdapClient, RdapResult, build_rdap_client
from app.modules.audit_web.checks import CheckResult
from app.modules.audit_web.models import AuditStatus
from app.modules.domain_intel import dns_lookup
from app.modules.domain_intel.evidence import CHECK_KEY as CHECK_KEY
from app.modules.domain_intel.models import DomainIntel
from app.modules.domain_intel.selection import DomainTarget, select

logger = get_logger("app.domain_intel")

BUDGET_SECONDS = 15.0
# Recorded DNS answers for development and CI. Absent from the production image, where the
# live resolver answers instead; an unrecorded name is `unknown` (decision C13).
DNS_FIXTURE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "tests",
    "fixtures",
    "dns",
)


@dataclass
class DomainTools:
    resolver: Resolver
    rdap: RdapClient


def build_tools(settings: Settings) -> DomainTools:
    if settings.fixtures_allowed:
        resolver: Resolver = FixtureResolver.from_directory(DNS_FIXTURE_DIR)
    else:
        resolver = LiveResolver(timeout=settings.dns_resolver_timeout_seconds)
    return DomainTools(resolver=resolver, rdap=build_rdap_client(settings=settings))


def enabled(settings: Settings) -> bool:
    return settings.dns_enabled or settings.rdap_enabled


@dataclass
class _Lookup:
    dns: dict[str, Any] | None = None
    rdap: dict[str, Any] | None = None
    dns_cached: bool = False
    rdap_cached: bool = False
    reasons: dict[str, str] = field(default_factory=dict)


def check(
    session: Session,
    *,
    status: AuditStatus,
    final_url: str | None,
    website: str | None,
    tools: DomainTools,
    settings: Settings,
    now: datetime,
) -> CheckResult:
    """The `checks.domain_intel` entry for one audit. `None` value, with a reason, if skipped."""
    selection = select(status=status, final_url=final_url, website=website)
    if selection.target is None:
        return CheckResult(None, evidence_text=selection.skip_reason)
    target = selection.target
    found = _gather(session, target, tools=tools, settings=settings, now=now)
    value: dict[str, Any] = {
        "domain": target.apex,
        "site_host": target.site_host,
        "dns": found.dns,
        "rdap": found.rdap,
        "dns_cached": found.dns_cached,
        "rdap_cached": found.rdap_cached,
        "reasons": found.reasons,
    }
    return CheckResult(value)


def _gather(
    session: Session,
    target: DomainTarget,
    *,
    tools: DomainTools,
    settings: Settings,
    now: datetime,
) -> _Lookup:
    row = session.get(DomainIntel, target.apex)
    found = _Lookup()
    ask_dns = ask_rdap = False

    if not settings.dns_enabled:
        found.reasons["dns"] = "DNS_ENABLED is off"
    elif row is not None and _fresh_dns(row, target, settings, now):
        found.dns, found.dns_cached = row.dns, True
    else:
        ask_dns = True

    if not settings.rdap_enabled:
        found.reasons["rdap"] = "RDAP_ENABLED is off"
    elif row is not None and _fresh(row.rdap, row.rdap_checked_at, settings.rdap_ttl_days, now):
        found.rdap, found.rdap_cached = row.rdap, True
    else:
        ask_rdap = True

    if not (ask_dns or ask_rdap):
        return found

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="domain-intel")
    try:
        dns_future: Future[dict[str, Any]] | None = None
        rdap_future: Future[RdapResult] | None = None
        if ask_dns:
            dns_future = pool.submit(
                dns_lookup.lookup, tools.resolver, target, budget_seconds=BUDGET_SECONDS, now=now
            )
        if ask_rdap:
            rdap_future = pool.submit(tools.rdap.lookup, target.apex)
        pending: list[Future[Any]] = [f for f in (dns_future, rdap_future) if f is not None]
        wait(pending, timeout=BUDGET_SECONDS)

        fresh_dns = fresh_rdap = None
        if dns_future is not None:
            fresh_dns = _result(dns_future, "dns", found)
            found.dns = fresh_dns
        if rdap_future is not None:
            result = _result(rdap_future, "rdap", found)
            if result is not None:
                fresh_rdap = result.snapshot(checked_at=now)
                if fresh_rdap is None and result.reason:
                    found.reasons["rdap"] = result.reason
            found.rdap = fresh_rdap
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    _save(
        session,
        target.apex,
        row,
        dns=fresh_dns if fresh_dns is not None and _certain(fresh_dns) else None,
        rdap=fresh_rdap,
        now=now,
    )
    return found


def _result[T](future: "Future[T]", half: str, found: _Lookup) -> T | None:
    if not future.done():
        found.reasons[half] = f"over the {BUDGET_SECONDS:g}-second domain lookup budget"
        return None
    error = future.exception()
    if error is not None:
        # The class only: nothing a registry or resolver said is logged.
        logger.warning("domain lookup failed", extra={"half": half, "error": type(error).__name__})
        found.reasons[half] = f"lookup failed ({type(error).__name__})"
        return None
    return future.result()


def _certain(snapshot: dict[str, Any]) -> bool:
    """Worth caching: the zone answered at least one question with `NOERROR`."""
    return any(item["rcode"] == Rcode.noerror.value for item in snapshot.get("queries", []))


def _fresh_dns(row: DomainIntel, target: DomainTarget, settings: Settings, now: datetime) -> bool:
    if row.dns is None or row.dns.get("site_host") != target.site_host:
        return False
    return _fresh(row.dns, row.dns_checked_at, settings.dns_intel_ttl_days, now)


def _fresh(
    snapshot: dict[str, Any] | None, checked_at: datetime | None, ttl_days: int, now: datetime
) -> bool:
    if snapshot is None or checked_at is None or ttl_days <= 0:
        return False
    return now - checked_at < timedelta(days=ttl_days)


def _save(
    session: Session,
    domain: str,
    row: DomainIntel | None,
    *,
    dns: dict[str, Any] | None,
    rdap: dict[str, Any] | None,
    now: datetime,
) -> None:
    """Store only what was answered with certainty; an error never overwrites a fact."""
    values: dict[str, Any] = {}
    if dns is not None:
        values.update(dns=dns, dns_checked_at=now)
    if rdap is not None:
        values.update(rdap=rdap, rdap_checked_at=now)
    if not values:
        return
    statement = insert(DomainIntel).values(domain=domain, **values)
    statement = statement.on_conflict_do_update(
        index_elements=[DomainIntel.domain],
        set_={**values, "updated_at": now},
    )
    session.execute(statement)
    if row is not None:
        # The upsert bypassed the ORM; the loaded row must be read again, not trusted.
        session.expire(row)
