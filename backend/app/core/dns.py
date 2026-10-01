"""DNS lookups behind one small interface (spec v0.13.0, task 2).

The rule this module exists for is *certainty or silence*. Only a `NOERROR` answer with no
record of the asked type proves the record is absent. `SERVFAIL`, `REFUSED`, a timeout or a
transport error proves nothing, so it is `unknown` — a failure that reads like an answer is
the same defect as a bot-challenge page read as a homepage (v0.12.0).

`NXDOMAIN` needs care. On the apex it is `unknown`: hijacking resolvers return it for names
that exist. Below the apex (`_dmarc.<apex>`, `www.<apex>`) it is the normal way a missing
name answers, so there it is `absent` — but only when the apex itself answered `NOERROR` in
the same run, which shows this resolver is telling the truth about the zone (decision C9).

Two resolvers implement the interface: `LiveResolver`, which asks the system's configured
nameserver over UDP (TCP on truncation), and `FixtureResolver`, which answers from recorded
JSON and is the only one development, CI and the test suite ever use (decision C13).
"""

import enum
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver


class Rcode(enum.StrEnum):
    """What came back. The first four are DNS response codes; the rest are ours."""

    noerror = "NOERROR"
    nxdomain = "NXDOMAIN"
    servfail = "SERVFAIL"
    refused = "REFUSED"
    # Any other response code (FORMERR, NOTIMP, …).
    other = "OTHER"
    timeout = "TIMEOUT"
    # A transport error, or — for the fixture resolver — a name nothing was recorded for.
    error = "ERROR"


class Outcome(enum.StrEnum):
    answered = "answered"
    absent = "absent"
    unknown = "unknown"


@dataclass(frozen=True)
class DnsAnswer:
    """One query's result: the values, the response code, and the nameserver that gave it."""

    name: str
    rdtype: str
    rcode: Rcode
    values: tuple[str, ...] = ()
    resolver: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rdtype": self.rdtype,
            "rcode": self.rcode.value,
            "values": list(self.values),
            "resolver": self.resolver,
        }


class Resolver(Protocol):
    def query(self, name: str, rdtype: str) -> DnsAnswer: ...


def outcome(answer: DnsAnswer, *, below_apex: bool = False, apex_answered: bool = False) -> Outcome:
    """Map one answer to `answered` / `absent` / `unknown`. Only `absent` may be reported.

    `apex_answered` is whether the apex's own queries answered `NOERROR` in this run; it only
    matters for a name below the apex that answered `NXDOMAIN`.
    """
    if answer.rcode is Rcode.noerror:
        return Outcome.answered if answer.values else Outcome.absent
    if answer.rcode is Rcode.nxdomain and below_apex and apex_answered:
        return Outcome.absent
    return Outcome.unknown


# --- live -------------------------------------------------------------------------------

_RCODES = {
    dns.rcode.NOERROR: Rcode.noerror,
    dns.rcode.NXDOMAIN: Rcode.nxdomain,
    dns.rcode.SERVFAIL: Rcode.servfail,
    dns.rcode.REFUSED: Rcode.refused,
}
# Worth asking once more: nothing came back, or the server said it could not answer now.
_RETRY_RCODES = frozenset({Rcode.timeout, Rcode.error, Rcode.servfail})


class LiveResolver:
    """Asks the system's nameserver. One query and a single retry share one time budget."""

    def __init__(
        self,
        *,
        timeout: float,
        nameservers: Iterable[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = timeout
        servers = list(nameservers) if nameservers is not None else _system_nameservers()
        self._nameservers = servers
        self._clock = clock

    def query(self, name: str, rdtype: str) -> DnsAnswer:
        if not self._nameservers:
            return DnsAnswer(name, rdtype, Rcode.error)
        deadline = self._clock() + self._timeout
        answer = DnsAnswer(name, rdtype, Rcode.timeout, resolver=self._nameservers[0])
        for attempt in range(2):
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            server = self._nameservers[attempt % len(self._nameservers)]
            answer = self._ask(name, rdtype, server, remaining)
            if answer.rcode not in _RETRY_RCODES:
                break
        return answer

    def _ask(self, name: str, rdtype: str, server: str, timeout: float) -> DnsAnswer:
        request = dns.message.make_query(name, rdtype)
        try:
            response = dns.query.udp(request, server, timeout=timeout)
            if response.flags & dns.flags.TC:
                response = dns.query.tcp(request, server, timeout=timeout)
        except dns.exception.Timeout:
            return DnsAnswer(name, rdtype, Rcode.timeout, resolver=server)
        except (OSError, dns.exception.DNSException):
            return DnsAnswer(name, rdtype, Rcode.error, resolver=server)
        rcode = _RCODES.get(response.rcode(), Rcode.other)
        wanted = dns.rdatatype.from_text(rdtype)
        values = tuple(
            _value(rdata) for rrset in response.answer if rrset.rdtype == wanted for rdata in rrset
        )
        return DnsAnswer(name, rdtype, rcode, values if rcode is Rcode.noerror else (), server)


def _value(rdata: Any) -> str:
    if rdata.rdtype == dns.rdatatype.TXT:
        return b"".join(rdata.strings).decode("utf-8", errors="replace")
    return str(rdata.to_text())


def _system_nameservers() -> list[str]:
    try:
        return [str(server) for server in dns.resolver.Resolver().nameservers]
    except dns.exception.DNSException:
        return []


# --- fixtures ---------------------------------------------------------------------------

FIXTURE_RESOLVER_NAME = "fixture"


@dataclass
class FixtureResolver:
    """Answers from recorded JSON files; a name or type nothing recorded is `unknown`.

    Each file is `{"answers": {"<name>": {"<TYPE>": {"rcode": ..., "values": [...]}}}}`.
    `queries` lists every question asked, so a test can prove none were.
    """

    records: Mapping[str, Mapping[str, Mapping[str, Any]]]
    queries: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_directory(cls, directory: str | None) -> "FixtureResolver":
        merged: dict[str, dict[str, Mapping[str, Any]]] = {}
        if directory and os.path.isdir(directory):
            for filename in sorted(os.listdir(directory)):
                if filename.endswith(".json"):
                    with open(os.path.join(directory, filename), encoding="utf-8") as handle:
                        for name, types in json.load(handle).get("answers", {}).items():
                            merged.setdefault(name.lower(), {}).update(types)
        return cls(merged)

    def query(self, name: str, rdtype: str) -> DnsAnswer:
        self.queries.append((name, rdtype))
        recorded = self.records.get(name.lower(), {}).get(rdtype)
        if recorded is None:
            return DnsAnswer(name, rdtype, Rcode.error, resolver=FIXTURE_RESOLVER_NAME)
        return DnsAnswer(
            name,
            rdtype,
            Rcode(recorded["rcode"]),
            tuple(recorded.get("values", ())),
            recorded.get("resolver", FIXTURE_RESOLVER_NAME),
        )
