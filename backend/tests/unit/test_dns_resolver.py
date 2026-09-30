"""The resolver and its certainty rule (spec v0.13.0, task 2 and acceptance 1-2).

Only `NOERROR` with no record of the asked type is `absent`. Every failure — `SERVFAIL`,
`REFUSED`, a timeout, a transport error, `NXDOMAIN` on the apex — is `unknown`. No test here
touches the network: the live resolver's transport is replaced by a function.
"""

import time
from datetime import UTC, datetime
from typing import Any

import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rrset
import pytest

from app.core import dns as dns_module
from app.core.dns import DnsAnswer, FixtureResolver, LiveResolver, Outcome, Rcode, outcome
from app.modules.domain_intel import dns_lookup
from app.modules.domain_intel.selection import DomainTarget

# --- answered / absent / unknown, one per response code ---------------------------------


def _answer(rcode: Rcode, *values: str, name: str = "acme.com") -> DnsAnswer:
    return DnsAnswer(name, "MX", rcode, tuple(values), "192.0.2.53")


def test_noerror_with_records_is_answered() -> None:
    assert outcome(_answer(Rcode.noerror, "10 mx.acme.com.")) is Outcome.answered


def test_noerror_without_records_is_absent() -> None:
    assert outcome(_answer(Rcode.noerror)) is Outcome.absent


def test_nxdomain_on_the_apex_is_unknown() -> None:
    # Hijacking resolvers answer NXDOMAIN for names that exist.
    assert outcome(_answer(Rcode.nxdomain), below_apex=False, apex_answered=True) is Outcome.unknown


def test_nxdomain_below_an_apex_that_answered_is_absent() -> None:
    answer = _answer(Rcode.nxdomain, name="_dmarc.acme.com")
    assert outcome(answer, below_apex=True, apex_answered=True) is Outcome.absent


def test_nxdomain_below_an_apex_that_did_not_answer_is_unknown() -> None:
    answer = _answer(Rcode.nxdomain, name="_dmarc.acme.com")
    assert outcome(answer, below_apex=True, apex_answered=False) is Outcome.unknown


@pytest.mark.parametrize(
    "rcode", [Rcode.servfail, Rcode.refused, Rcode.timeout, Rcode.error, Rcode.other]
)
def test_every_failure_is_unknown(rcode: Rcode) -> None:
    assert outcome(_answer(rcode), below_apex=True, apex_answered=True) is Outcome.unknown


# --- the live resolver, with its transport replaced --------------------------------------


def _response(request: dns.message.Message, rcode: int, *rdata: str) -> dns.message.Message:
    response = dns.message.make_response(request)
    response.set_rcode(rcode)
    question = request.question[0]
    for value in rdata:
        response.answer.append(
            dns.rrset.from_text(question.name, 300, "IN", question.rdtype, value)
        )
    return response


class _Transport:
    """Stands in for `dns.query.udp` / `dns.query.tcp`: a scripted answer per call."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, request: dns.message.Message, where: str, timeout: float) -> Any:
        self.calls.append((where, timeout))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(request)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(*script: Any, tcp: Any = None) -> _Transport:
        udp = _Transport(*script)
        monkeypatch.setattr(dns_module.dns.query, "udp", udp)
        if tcp is not None:
            monkeypatch.setattr(dns_module.dns.query, "tcp", tcp)
        return udp

    return install


def test_live_answer_records_values_rcode_and_the_resolver_used(transport: Any) -> None:
    transport(lambda req: _response(req, dns.rcode.NOERROR, "10 mx1.acme.com.", "20 mx2.acme.com."))
    answer = LiveResolver(timeout=5, nameservers=["192.0.2.53"]).query("acme.com", "MX")
    assert answer.rcode is Rcode.noerror
    assert answer.values == ("10 mx1.acme.com.", "20 mx2.acme.com.")
    assert answer.resolver == "192.0.2.53"


def test_live_txt_strings_are_joined(transport: Any) -> None:
    transport(
        lambda req: _response(req, dns.rcode.NOERROR, '"v=spf1 include:_spf.google.com" " -all"')
    )
    answer = LiveResolver(timeout=5, nameservers=["192.0.2.53"]).query("acme.com", "TXT")
    assert answer.values == ("v=spf1 include:_spf.google.com -all",)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (dns.rcode.NXDOMAIN, Rcode.nxdomain),
        (dns.rcode.REFUSED, Rcode.refused),
        (dns.rcode.FORMERR, Rcode.other),
    ],
)
def test_live_failure_codes_are_kept_as_they_came(
    transport: Any, code: int, expected: Rcode
) -> None:
    transport(lambda req: _response(req, code))
    answer = LiveResolver(timeout=5, nameservers=["192.0.2.53"]).query("acme.com", "A")
    assert answer.rcode is expected
    assert answer.values == ()


def test_a_timeout_is_retried_once_then_answers(transport: Any) -> None:
    udp = transport(
        dns.exception.Timeout(), lambda req: _response(req, dns.rcode.NOERROR, "192.0.2.1")
    )
    answer = LiveResolver(timeout=5, nameservers=["192.0.2.53"]).query("acme.com", "A")
    assert answer.rcode is Rcode.noerror
    assert len(udp.calls) == 2


def test_servfail_twice_is_servfail_after_a_single_retry(transport: Any) -> None:
    udp = transport(
        lambda req: _response(req, dns.rcode.SERVFAIL),
        lambda req: _response(req, dns.rcode.SERVFAIL),
    )
    answer = LiveResolver(timeout=5, nameservers=["192.0.2.53"]).query("acme.com", "A")
    assert answer.rcode is Rcode.servfail
    assert len(udp.calls) == 2


def test_the_retry_shares_the_one_time_budget(transport: Any) -> None:
    ticks = iter([0.0, 0.0, 3.5, 99.0])
    udp = transport(dns.exception.Timeout(), dns.exception.Timeout())
    resolver = LiveResolver(timeout=5, nameservers=["192.0.2.53"], clock=lambda: next(ticks))
    answer = resolver.query("acme.com", "A")
    assert answer.rcode is Rcode.timeout
    assert [round(timeout, 1) for _, timeout in udp.calls] == [5.0, 1.5]


def test_a_transport_error_is_error(transport: Any) -> None:
    transport(OSError("network unreachable"), OSError("network unreachable"))
    answer = LiveResolver(timeout=5, nameservers=["192.0.2.53"]).query("acme.com", "A")
    assert answer.rcode is Rcode.error


def test_a_truncated_answer_is_asked_again_over_tcp(transport: Any) -> None:
    def truncated(req: dns.message.Message) -> dns.message.Message:
        response = _response(req, dns.rcode.NOERROR)
        response.flags |= dns.flags.TC
        return response

    tcp = _Transport(lambda req: _response(req, dns.rcode.NOERROR, '"v=spf1 -all"'))
    transport(truncated, tcp=tcp)
    answer = LiveResolver(timeout=5, nameservers=["192.0.2.53"]).query("acme.com", "TXT")
    assert answer.values == ("v=spf1 -all",)
    assert len(tcp.calls) == 1


def test_no_nameserver_is_error() -> None:
    assert LiveResolver(timeout=5, nameservers=[]).query("acme.com", "A").rcode is Rcode.error


# --- the fixture resolver --------------------------------------------------------------


def test_fixture_resolver_answers_what_was_recorded_and_nothing_else() -> None:
    resolver = FixtureResolver(
        {"acme.com": {"MX": {"rcode": "NOERROR", "values": [], "resolver": "192.0.2.53"}}}
    )
    recorded = resolver.query("acme.com", "MX")
    assert (recorded.rcode, recorded.resolver) == (Rcode.noerror, "192.0.2.53")
    unrecorded = resolver.query("acme.com", "A")
    assert unrecorded.rcode is Rcode.error
    assert outcome(unrecorded) is Outcome.unknown
    assert resolver.queries == [("acme.com", "MX"), ("acme.com", "A")]


def test_fixture_resolver_with_no_directory_answers_nothing() -> None:
    resolver = FixtureResolver.from_directory("/nonexistent/dns-fixtures")
    assert resolver.query("acme.com", "A").rcode is Rcode.error


# --- the questions and the snapshot ------------------------------------------------------

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TARGET = DomainTarget(site_host="www.acme.com", apex="acme.com")


def test_the_nine_questions() -> None:
    assert dns_lookup.questions(TARGET) == [
        ("www.acme.com", "A"),
        ("www.acme.com", "AAAA"),
        ("www.acme.com", "CNAME"),
        ("acme.com", "A"),
        ("acme.com", "AAAA"),
        ("acme.com", "NS"),
        ("acme.com", "MX"),
        ("acme.com", "TXT"),
        ("_dmarc.acme.com", "TXT"),
    ]


def test_a_site_served_from_the_apex_is_not_asked_twice() -> None:
    asked = dns_lookup.questions(DomainTarget(site_host="acme.com", apex="acme.com"))
    assert len(asked) == len(set(asked)) == 7


def _ok(*values: str) -> dict[str, Any]:
    return {"rcode": "NOERROR", "values": list(values), "resolver": "192.0.2.53"}


def test_snapshot_keeps_spf_and_dmarc_tags_only() -> None:
    resolver = FixtureResolver(
        {
            "acme.com": {
                "NS": _ok("ns1.domaincontrol.com.", "ns2.domaincontrol.com."),
                "MX": _ok("1 aspmx.l.google.com."),
                "TXT": _ok(
                    "v=spf1 include:_spf.google.com ~all",
                    "google-site-verification=abc123",
                    "owner is jane.doe@acme.com",
                ),
            },
            "_dmarc.acme.com": {
                "TXT": _ok(
                    "v=DMARC1; p=none; sp=reject; rua=mailto:jane.doe@acme.com; ruf=mailto:x@y.com"
                )
            },
        }
    )
    snap = dns_lookup.lookup(resolver, TARGET, budget_seconds=5, now=NOW)
    assert snap["spf"] == ["v=spf1 include:_spf.google.com ~all"]
    assert snap["txt_other_count"] == 2
    assert snap["dmarc"] == [{"v": "DMARC1", "p": "none", "sp": "reject"}]
    assert snap["providers"] == {"dns": ["GoDaddy"], "mail": ["Google Workspace"]}
    assert snap["source"] == "dns"
    assert snap["resolvers"] == ["192.0.2.53", "fixture"]
    text = repr(snap)
    for leaked in ("jane.doe", "mailto", "google-site-verification", "rua", "ruf", "x@y.com"):
        assert leaked not in text


def test_dmarc_nxdomain_is_absent_when_the_apex_answered() -> None:
    resolver = FixtureResolver(
        {
            "acme.com": {"NS": _ok("ns1.acme.com.")},
            "_dmarc.acme.com": {"TXT": {"rcode": "NXDOMAIN", "values": []}},
        }
    )
    snap = dns_lookup.lookup(resolver, TARGET, budget_seconds=5, now=NOW)
    assert dns_lookup.state(snap, "_dmarc.acme.com", "TXT") is Outcome.absent


def test_dmarc_nxdomain_is_unknown_when_the_apex_did_not_answer() -> None:
    resolver = FixtureResolver({"_dmarc.acme.com": {"TXT": {"rcode": "NXDOMAIN", "values": []}}})
    snap = dns_lookup.lookup(resolver, TARGET, budget_seconds=5, now=NOW)
    assert dns_lookup.state(snap, "_dmarc.acme.com", "TXT") is Outcome.unknown


def test_a_question_still_running_at_the_deadline_is_unknown() -> None:
    class Slow:
        def query(self, name: str, rdtype: str) -> DnsAnswer:
            if rdtype == "MX":
                time.sleep(0.5)
            return DnsAnswer(name, rdtype, Rcode.noerror, (), "192.0.2.53")

    snap = dns_lookup.lookup(Slow(), TARGET, budget_seconds=0.1, now=NOW)
    assert dns_lookup.state(snap, "acme.com", "MX") is Outcome.unknown
    assert dns_lookup.entry(snap, "acme.com", "MX")["rcode"] == "TIMEOUT"  # type: ignore[index]
    assert dns_lookup.state(snap, "acme.com", "NS") is Outcome.absent


def test_a_resolver_that_raises_is_unknown() -> None:
    class Broken:
        def query(self, name: str, rdtype: str) -> DnsAnswer:
            raise RuntimeError("boom")

    snap = dns_lookup.lookup(Broken(), TARGET, budget_seconds=1, now=NOW)
    assert {item["outcome"] for item in snap["queries"]} == {"unknown"}
