"""Domain intelligence smoke check: live DNS and RDAP for a few domains, run by a human.

    uv run --project backend python scripts/domain_intel_smoke.py acme-plumbing.com
    uv run --project backend python scripts/domain_intel_smoke.py https://www.acme.com/ b.com
    uv run --project backend python scripts/domain_intel_smoke.py --file domains.txt \\
        --record backend/tests/fixtures

Never part of the tests (spec v0.13.0, task 9). For each domain it prints the snapshot an
audit would store and the domain findings that snapshot produces, so a human can check
them against reality. Nothing is written to the database and no audit is made.

Each argument may be a domain or a URL; it goes through the same selection an audit uses
(IDNA, the Public Suffix List, the shared-domain deny list), so a builder or social host is
reported as skipped and never asked about.

The homepage is not fetched, so `domain_no_a_record` is shown as the snapshot would report
it with no HTTP answer; an audit that got any HTTP answer suppresses it (decision C10).
`--site-answered` shows that case instead.

Calls: up to nine DNS questions and one RDAP call per domain. RDAP calls are spaced one
second apart (`RDAP_RPS` 1) and go through `app/core/http.py`, but not through the daily
cap or `api_calls` — like `psi_smoke.py`, this is an operator's tool, and the count it
prints is the whole of what it spent. `--max` (default 25) is a hard ceiling.

`--record DIR` writes one DNS fixture (`DIR/dns/recorded_<domain>.json`) and one RDAP
fixture (`DIR/rdap/recorded_<domain>.json`) per domain, redacted first so no person's data
is ever committed (Goal rule 2, decision C8): apex TXT records other than SPF are replaced
by a placeholder, a DMARC record keeps only its `v`, `p` and `sp` tags, and an RDAP answer
keeps only the registrar entity's name. The redacted copy is checked to produce exactly the
same snapshot as the live answer before anything is written.
"""

import argparse
import copy
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.dns import DnsAnswer, FixtureResolver, LiveResolver, Resolver
from app.core.http import ApiHttpClient
from app.core.rdap import (
    RDAP_SOURCE_NAME,
    RDAP_TIMEOUT,
    RdapFacts,
    RdapResult,
    bootstrap,
    parse_domain,
)
from app.modules.adapters.errors import AdapterError
from app.modules.audit_web.models import AuditStatus
from app.modules.domain_intel import dns_lookup, records
from app.modules.domain_intel.findings import for_domain
from app.modules.domain_intel.selection import DomainTarget, select
from app.modules.domain_intel.service import BUDGET_SECONDS

REDACTED_TXT = "redacted-non-spf-txt-record"
RDAP_SPACING_SECONDS = 1.0


# --- redaction (tested in backend/tests/unit/test_domain_intel_smoke.py) ------------------


def redact_dns(
    target: DomainTarget, answers: Mapping[tuple[str, str], DnsAnswer]
) -> dict[str, Any]:
    """The fixture form of `answers`, with nothing that could name a person."""
    recorded: dict[str, dict[str, Any]] = {}
    for (name, rdtype), answer in answers.items():
        values = list(answer.values)
        if rdtype == "TXT" and name == target.apex:
            split = records.split_apex_txt(values)
            values = [*split.spf, *[REDACTED_TXT] * split.other_count]
        elif rdtype == "TXT" and name == dns_lookup.dmarc_name(target.apex):
            parsed = records.split_dmarc_txt(values)
            values = [records.dmarc_text(tags) for tags in parsed.records]
            values += [REDACTED_TXT] * parsed.other_count
        recorded.setdefault(name, {})[rdtype] = {
            "rcode": answer.rcode.value,
            "values": values,
            "resolver": answer.resolver,
        }
    return recorded


def redact_rdap(body: Any) -> dict[str, Any]:
    """An RDAP answer with every entity gone except the registrar's name."""
    if not isinstance(body, Mapping):
        return {}
    kept: dict[str, Any] = {
        key: copy.deepcopy(body[key])
        for key in ("objectClassName", "ldhName", "status", "events", "links")
        if key in body
    }
    for entity in body.get("entities") or []:
        if not isinstance(entity, Mapping) or "registrar" not in (entity.get("roles") or []):
            continue
        name = _vcard_fn(entity)
        registrar: dict[str, Any] = {"objectClassName": "entity", "roles": ["registrar"]}
        if name is not None:
            registrar["vcardArray"] = [
                "vcard",
                [["version", {}, "text", "4.0"], ["fn", {}, "text", name]],
            ]
        kept["entities"] = [registrar]
        break
    return kept


def _vcard_fn(entity: Mapping[str, Any]) -> str | None:
    vcard = entity.get("vcardArray")
    if not (isinstance(vcard, list) and len(vcard) == 2 and isinstance(vcard[1], list)):
        return None
    for item in vcard[1]:
        if isinstance(item, list) and len(item) == 4 and item[0] == "fn":
            return str(item[3])
    return None


# --- one domain --------------------------------------------------------------------------


class RecordingResolver:
    """Asks the live resolver and keeps every answer, so it can be recorded."""

    def __init__(self, inner: Resolver) -> None:
        self.inner = inner
        self.answers: dict[tuple[str, str], DnsAnswer] = {}

    def query(self, name: str, rdtype: str) -> DnsAnswer:
        answer = self.inner.query(name, rdtype)
        self.answers[(name, rdtype)] = answer
        return answer


def rdap_lookup(http: ApiHttpClient, domain: str, user_agent: str) -> tuple[RdapResult, Any]:
    """One RDAP call; the result and the raw body (kept in memory only, for redaction)."""
    base = bootstrap().base_url(domain)
    if base is None:
        return RdapResult(None, None, "no RDAP endpoint for this TLD"), None
    url = f"{base}domain/{domain}"
    try:
        body = http.request_json(
            "GET",
            url,
            parse=lambda raw: raw,
            headers={"Accept": "application/rdap+json", "User-Agent": user_agent},
        )
    except AdapterError as exc:
        return RdapResult(None, url, f"RDAP lookup failed ({type(exc).__name__})"), None
    facts: RdapFacts | None = parse_domain(body)
    if facts is None:
        return RdapResult(None, url, "RDAP answer had no expiry date"), body
    return RdapResult(facts, url), body


def check_one(
    raw: str,
    *,
    resolver: Resolver,
    http: ApiHttpClient,
    settings: Settings,
    site_answered: bool,
    record: Path | None,
    now: datetime,
    out: Any = sys.stdout,
) -> int:
    """Print one domain's snapshot and findings. Returns how many RDAP calls were made."""
    selection = select(status=AuditStatus.done, final_url=raw, website=None)
    if selection.target is None:
        print(f"== {raw}: skipped — {selection.skip_reason}", file=out)
        return 0
    target = selection.target
    recording = RecordingResolver(resolver)
    dns = dns_lookup.lookup(recording, target, budget_seconds=BUDGET_SECONDS, now=now)
    result, body = rdap_lookup(http, target.apex, settings.user_agent)
    rdap = result.snapshot(checked_at=now)
    value = {"domain": target.apex, "site_host": target.site_host, "dns": dns, "rdap": rdap}
    if rdap is None:
        value["reasons"] = {"rdap": result.reason}
    findings = for_domain(
        value,
        status=AuditStatus.done,
        site_answered=site_answered,
        now=now,
        expiry_warn_days=settings.audit_domain_expiry_warn_days,
    )
    print(f"== {raw} → {target.site_host} (registrable domain {target.apex})", file=out)
    print(json.dumps(value, indent=2, sort_keys=True), file=out)
    print(f"-- {len(findings)} finding(s):", file=out)
    for finding in findings:
        print(f"   [{finding.severity.value}] {finding.code}: {finding.message}", file=out)
        print(f"      evidence: {finding.evidence_text}", file=out)
    if record is not None:
        write_fixtures(record, target, recording.answers, body, now=now, out=out)
    return 0 if result.url is None else 1


def write_fixtures(
    root: Path,
    target: DomainTarget,
    answers: Mapping[tuple[str, str], DnsAnswer],
    body: Any,
    *,
    now: datetime,
    out: Any = sys.stdout,
) -> None:
    """Write the redacted fixtures, after proving they reproduce the live snapshot."""
    recorded = redact_dns(target, answers)
    replay = FixtureResolver({name.lower(): types for name, types in recorded.items()})
    live = dns_lookup.snapshot(target, dict(answers), checked_at=now)
    again = dns_lookup.lookup(replay, target, budget_seconds=BUDGET_SECONDS, now=now)
    if _without_resolvers(live) != _without_resolvers(again):
        print(f"   not recorded: the redacted DNS answers differ for {target.apex}", file=out)
        return
    stamp = now.date().isoformat()
    dns_file = root / "dns" / f"recorded_{target.apex}.json"
    dns_file.parent.mkdir(parents=True, exist_ok=True)
    _write(
        dns_file,
        {"_comment": f"Recorded {stamp} by domain_intel_smoke.py, redacted.", "answers": recorded},
    )
    print(f"   recorded {dns_file}", file=out)
    if body is None:
        return
    redacted = redact_rdap(body)
    if parse_domain(redacted) != parse_domain(body):
        print(f"   not recorded: the redacted RDAP answer differs for {target.apex}", file=out)
        return
    rdap_file = root / "rdap" / f"recorded_{target.apex}.json"
    rdap_file.parent.mkdir(parents=True, exist_ok=True)
    _write(
        rdap_file,
        {
            "_comment": (
                f"Recorded {stamp} by domain_intel_smoke.py; only the registrar's name "
                "kept from any entity."
            ),
            **redacted,
        },
    )
    print(f"   recorded {rdap_file}", file=out)


def _without_resolvers(snapshot: dict[str, Any]) -> dict[str, Any]:
    kept = {key: value for key, value in snapshot.items() if key != "resolvers"}
    kept["queries"] = [
        {key: value for key, value in query.items() if key != "resolver"}
        for query in snapshot.get("queries", [])
    ]
    return kept


def _write(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def domains_from(args: argparse.Namespace) -> list[str]:
    found: list[str] = list(args.domains)
    if args.file is not None:
        for line in args.file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                found.append(line)
    return list(dict.fromkeys(found))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("domains", nargs="*", help="domains or URLs")
    parser.add_argument("--file", type=Path, help="one domain or URL per line")
    parser.add_argument("--max", type=int, default=25, help="hard ceiling (default 25)")
    parser.add_argument("--site-answered", action="store_true", help="as if the homepage answered")
    parser.add_argument("--record", type=Path, help="fixture root, e.g. backend/tests/fixtures")
    args = parser.parse_args(argv)
    wanted = domains_from(args)
    if not wanted:
        parser.error("give at least one domain, or --file")
    if len(wanted) > args.max:
        print(
            f"{len(wanted)} domains is over --max {args.max}; nothing was asked.", file=sys.stderr
        )
        return 2

    settings = get_settings()
    resolver = LiveResolver(timeout=settings.dns_resolver_timeout_seconds)
    http = ApiHttpClient(source=RDAP_SOURCE_NAME, max_attempts=1, timeout=RDAP_TIMEOUT)
    now = datetime.now(UTC)
    calls = 0
    for index, raw in enumerate(wanted):
        if index and calls:
            time.sleep(RDAP_SPACING_SECONDS)
        calls += check_one(
            raw,
            resolver=resolver,
            http=http,
            settings=settings,
            site_answered=args.site_answered,
            record=args.record,
            now=now,
        )
    print(f"\n{len(wanted)} domain(s), {calls} RDAP call(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
