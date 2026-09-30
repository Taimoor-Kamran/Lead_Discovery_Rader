"""The DNS half of a domain snapshot (spec v0.13.0, task 2).

Nine questions at most, asked concurrently inside one deadline: `A`, `AAAA` and `CNAME` on
the host the site was served from; `A`, `AAAA`, `NS`, `MX` and `TXT` on the apex; `TXT` on
`_dmarc.<apex>`. A question that has not answered by the deadline is `unknown`, and `unknown`
never becomes a finding (decision C15).

The snapshot keeps what the findings need and no more (decision C8): apex TXT keeps only its
SPF records and a count of the rest; `_dmarc` keeps only the `v`, `p` and `sp` tags.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from typing import Any

from app.core.dns import DnsAnswer, Outcome, Rcode, Resolver, outcome
from app.modules.domain_intel import records
from app.modules.domain_intel.selection import DomainTarget

DNS_SOURCE = "dns"
SITE_TYPES = ("A", "AAAA", "CNAME")
APEX_TYPES = ("A", "AAAA", "NS", "MX", "TXT")

# Name servers and mail servers, mapped to who runs them. Stored as a fact; never a finding.
_DNS_PROVIDERS = (
    ("domaincontrol.com", "GoDaddy"),
    ("cloudflare.com", "Cloudflare"),
    ("registrar-servers.com", "Namecheap"),
    ("awsdns", "Amazon Route 53"),
    ("googledomains.com", "Google Domains"),
    ("squarespacedns.com", "Squarespace"),
    ("wixdns.net", "Wix"),
    ("azure-dns", "Microsoft Azure"),
    ("nsone.net", "NS1"),
    ("hostgator.com", "HostGator"),
    ("bluehost.com", "Bluehost"),
    ("ionos", "IONOS"),
    ("siteground.net", "SiteGround"),
    ("dreamhost.com", "DreamHost"),
    ("name.com", "Name.com"),
    ("worldnic.com", "Network Solutions"),
)
_MAIL_PROVIDERS = (
    ("google.com", "Google Workspace"),
    ("googlemail.com", "Google Workspace"),
    ("outlook.com", "Microsoft 365"),
    ("secureserver.net", "GoDaddy"),
    ("zoho.com", "Zoho Mail"),
    ("protonmail.ch", "Proton Mail"),
    ("mimecast.com", "Mimecast"),
    ("pphosted.com", "Proofpoint"),
    ("messagingengine.com", "Fastmail"),
    ("registrar-servers.com", "Namecheap"),
)


def questions(target: DomainTarget) -> list[tuple[str, str]]:
    """Every (name, type) asked, without repeats when the site is served from the apex."""
    asked: list[tuple[str, str]] = [(target.site_host, rdtype) for rdtype in SITE_TYPES]
    asked += [(target.apex, rdtype) for rdtype in APEX_TYPES]
    asked.append((dmarc_name(target.apex), "TXT"))
    unique: list[tuple[str, str]] = []
    for question in asked:
        if question not in unique:
            unique.append(question)
    return unique


def dmarc_name(apex: str) -> str:
    return f"_dmarc.{apex}"


def lookup(
    resolver: Resolver,
    target: DomainTarget,
    *,
    budget_seconds: float,
    now: datetime,
) -> dict[str, Any]:
    """Ask every question, wait at most `budget_seconds`, and build the stored snapshot."""
    answers = _ask_all(resolver, questions(target), budget_seconds)
    return snapshot(target, answers, checked_at=now)


def _ask_all(
    resolver: Resolver, asked: list[tuple[str, str]], budget_seconds: float
) -> dict[tuple[str, str], DnsAnswer]:
    pool = ThreadPoolExecutor(max_workers=len(asked), thread_name_prefix="dns")
    try:
        futures = {
            pool.submit(resolver.query, name, rdtype): (name, rdtype) for name, rdtype in asked
        }
        wait(futures, timeout=max(budget_seconds, 0))
        answers: dict[tuple[str, str], DnsAnswer] = {}
        for future, (name, rdtype) in futures.items():
            if future.done() and future.exception() is None:
                answers[(name, rdtype)] = future.result()
            else:
                answers[(name, rdtype)] = DnsAnswer(name, rdtype, Rcode.timeout)
        return answers
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def snapshot(
    target: DomainTarget,
    answers: dict[tuple[str, str], DnsAnswer],
    *,
    checked_at: datetime,
) -> dict[str, Any]:
    apex_answers = [a for (name, _), a in answers.items() if name == target.apex]
    apex_answered = any(a.rcode is Rcode.noerror for a in apex_answers) and not any(
        a.rcode is Rcode.nxdomain for a in apex_answers
    )
    entries: list[dict[str, Any]] = []
    spf: list[str] = []
    txt_other = 0
    dmarc: list[dict[str, str]] = []
    dmarc_other = 0
    for (name, rdtype), answer in answers.items():
        below_apex = name != target.apex
        entry = answer.as_dict()
        entry["outcome"] = outcome(answer, below_apex=below_apex, apex_answered=apex_answered).value
        if rdtype == "TXT" and name == target.apex:
            split = records.split_apex_txt(answer.values)
            spf, txt_other = list(split.spf), split.other_count
            entry["values"] = list(split.spf)
        elif rdtype == "TXT" and name == dmarc_name(target.apex):
            parsed = records.split_dmarc_txt(answer.values)
            dmarc, dmarc_other = list(parsed.records), parsed.other_count
            entry["values"] = []
        entries.append(entry)
    resolvers = sorted({a.resolver for a in answers.values() if a.resolver})
    ns = _values(entries, target.apex, "NS")
    mx = _values(entries, target.apex, "MX")
    return {
        "source": DNS_SOURCE,
        "site_host": target.site_host,
        "apex": target.apex,
        "resolvers": resolvers,
        "checked_at": checked_at.isoformat(),
        "queries": entries,
        "spf": spf,
        "txt_other_count": txt_other,
        "dmarc": dmarc,
        "dmarc_other_count": dmarc_other,
        "providers": {
            "dns": _providers(ns, _DNS_PROVIDERS),
            "mail": _providers(mx, _MAIL_PROVIDERS),
        },
    }


def entry(snapshot_: dict[str, Any], name: str, rdtype: str) -> dict[str, Any] | None:
    for item in snapshot_.get("queries", []):
        if item["name"] == name and item["rdtype"] == rdtype:
            return dict(item)
    return None


def state(snapshot_: dict[str, Any], name: str, rdtype: str) -> Outcome:
    item = entry(snapshot_, name, rdtype)
    return Outcome(item["outcome"]) if item is not None else Outcome.unknown


def _values(entries: list[dict[str, Any]], name: str, rdtype: str) -> list[str]:
    for item in entries:
        if item["name"] == name and item["rdtype"] == rdtype:
            return list(item["values"])
    return []


def _providers(hosts: list[str], table: tuple[tuple[str, str], ...]) -> list[str]:
    found: list[str] = []
    for host in hosts:
        lowered = host.lower().rstrip(".")
        for marker, provider in table:
            if _matches(lowered, marker) and provider not in found:
                found.append(provider)
    return found


def _matches(host: str, marker: str) -> bool:
    """A dotted marker is a domain suffix; an undotted one a fragment (`awsdns-12.org`)."""
    if "." in marker:
        return host == marker or host.endswith(f".{marker}")
    return marker in host
