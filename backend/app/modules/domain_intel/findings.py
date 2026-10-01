"""The nine domain findings, read from an audit's stored `checks.domain_intel` (v0.13.0, task 5).

Certainty or silence: a DNS finding needs the question behind it to have been answered —
`absent` (`NOERROR` with no record of that type, or `NXDOMAIN` below an apex that answered)
or a record that shows the condition. `unknown` produces nothing, and so does a missing RDAP
snapshot. Everything here works from what the audit stored, so the audit row alone
reproduces its domain findings.

Evidence names its source (decision C17) and quotes the actual record or registry value;
`evidence_url` is `None`, since DNS has no URL. Only SPF records and the kept DMARC tags are
ever quoted (decision C8).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.core.dns import Outcome
from app.modules.audit_web.findings import Finding, build
from app.modules.audit_web.models import AuditStatus
from app.modules.domain_intel import dns_lookup, records

# Statuses a domain finding may be emitted on (task 5). Not `skipped`, `robots_blocked` or
# `failed`: none of them makes a lookup at all.
DOMAIN_FINDING_STATUSES = frozenset(
    {
        AuditStatus.done,
        AuditStatus.bot_challenge,
        AuditStatus.not_readable,
        AuditStatus.unreachable,
    }
)


def for_domain(
    value: Mapping[str, Any] | None,
    *,
    status: AuditStatus,
    site_answered: bool,
    now: datetime,
    expiry_warn_days: int,
) -> list[Finding]:
    """Every domain finding the stored snapshot supports, in catalogue order.

    `site_answered` is whether the site gave any HTTP answer in this audit — a status line
    from the homepage or from robots.txt. Any answer proves the host resolved, so
    `domain_no_a_record` is never reported beside one (decision C10).
    """
    if status not in DOMAIN_FINDING_STATUSES or not value:
        return []
    found: list[Finding] = []
    rdap = value.get("rdap")
    if isinstance(rdap, Mapping):
        domain = str(value.get("domain") or "")
        found += _registry_findings(rdap, domain=domain, now=now, warn_days=expiry_warn_days)
    dns = value.get("dns")
    if isinstance(dns, Mapping):
        found += _dns_findings(dns, site_answered=site_answered)
    order = list(_ORDER)
    return sorted(found, key=lambda finding: order.index(finding.code))


_ORDER = (
    "domain_expired",
    "domain_no_a_record",
    "domain_expiring_soon",
    "multiple_spf_records",
    "spf_allows_all",
    "no_spf",
    "no_dmarc",
    "dmarc_policy_none",
    "no_domain_mx",
)


# --- RDAP -------------------------------------------------------------------------------


def _registry_findings(
    rdap: Mapping[str, Any], *, domain: str, now: datetime, warn_days: int
) -> list[Finding]:
    expires = _when(rdap.get("expires"))
    if expires is None or not domain:
        return []
    evidence = f"RDAP {rdap.get('source_url')}: expiration event {rdap.get('expires')}"
    date = expires.date().isoformat()
    if expires <= now:
        return [build("domain_expired", domain=domain, date=date, evidence_text=evidence)]
    days = (expires - now).days
    if days <= warn_days:
        return [
            build(
                "domain_expiring_soon",
                domain=domain,
                date=date,
                days=days,
                evidence_text=evidence,
            )
        ]
    return []


def _when(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# --- DNS --------------------------------------------------------------------------------


def _dns_findings(dns: Mapping[str, Any], *, site_answered: bool) -> list[Finding]:
    snapshot = dict(dns)
    apex = str(snapshot.get("apex") or "")
    host = str(snapshot.get("site_host") or "")
    if not apex or not host:
        return []
    found: list[Finding] = []

    site = [(host, rdtype) for rdtype in dns_lookup.SITE_TYPES]
    if not site_answered and all(_state(snapshot, *q) is Outcome.absent for q in site):
        found.append(
            build(
                "domain_no_a_record",
                host=host,
                evidence_text=_evidence(snapshot, site, "no A, AAAA or CNAME record"),
            )
        )

    txt = _state(snapshot, apex, "TXT")
    if txt is not Outcome.unknown:
        spf = [str(record) for record in snapshot.get("spf") or []]
        if not spf:
            found.append(
                build(
                    "no_spf",
                    domain=apex,
                    evidence_text=_evidence(snapshot, [(apex, "TXT")], "no v=spf1 record"),
                )
            )
        elif len(spf) > 1:
            found.append(
                build(
                    "multiple_spf_records",
                    count=len(spf),
                    domain=apex,
                    evidence_text=_evidence(snapshot, [(apex, "TXT")], " | ".join(spf)),
                )
            )
        elif records.spf_allows_all(spf[0]):
            found.append(
                build(
                    "spf_allows_all",
                    domain=apex,
                    mechanism=records.spf_all(spf[0]) or "all",
                    evidence_text=_evidence(snapshot, [(apex, "TXT")], spf[0]),
                )
            )

    dmarc_name = dns_lookup.dmarc_name(apex)
    if _state(snapshot, dmarc_name, "TXT") is not Outcome.unknown:
        dmarc = [dict(record) for record in snapshot.get("dmarc") or []]
        if not dmarc:
            found.append(
                build(
                    "no_dmarc",
                    domain=apex,
                    evidence_text=_evidence(snapshot, [(dmarc_name, "TXT")], "no v=DMARC1 record"),
                )
            )
        elif len(dmarc) == 1 and records.dmarc_policy(dmarc[0]) == "none":
            found.append(
                build(
                    "dmarc_policy_none",
                    domain=apex,
                    evidence_text=_evidence(
                        snapshot, [(dmarc_name, "TXT")], records.dmarc_text(dmarc[0])
                    ),
                )
            )

    if _state(snapshot, apex, "MX") is Outcome.absent:
        found.append(
            build(
                "no_domain_mx",
                domain=apex,
                evidence_text=_evidence(snapshot, [(apex, "MX")], "no MX record"),
            )
        )
    return found


def _state(snapshot: dict[str, Any], name: str, rdtype: str) -> Outcome:
    return dns_lookup.state(snapshot, name, rdtype)


def _evidence(snapshot: dict[str, Any], asked: list[tuple[str, str]], what: str) -> str:
    """`DNS <name> <type> <rcode>, …: <what> (resolver <ip>, checked <time>)`."""
    parts = []
    resolvers: list[str] = []
    for name, rdtype in asked:
        entry = dns_lookup.entry(snapshot, name, rdtype) or {}
        parts.append(f"{name} {rdtype} {entry.get('rcode', 'unknown')}")
        resolver = entry.get("resolver")
        if resolver and resolver not in resolvers:
            resolvers.append(str(resolver))
    asked_by = ", ".join(resolvers) or "unknown"
    return (
        f"DNS {', '.join(parts)}: {what} "
        f"(resolver {asked_by}, checked {snapshot.get('checked_at')})"
    )
