"""Domain findings from a stored snapshot: certainty or silence (spec v0.13.0, task 5)."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.dns import DnsAnswer, Rcode
from app.modules.audit_web.models import AuditStatus
from app.modules.domain_intel import dns_lookup
from app.modules.domain_intel.findings import DOMAIN_FINDING_STATUSES, for_domain
from app.modules.domain_intel.selection import DomainTarget

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TARGET = DomainTarget(site_host="www.acme.com", apex="acme.com")
HEALTHY: dict[tuple[str, str], tuple[Rcode, tuple[str, ...]]] = {
    ("www.acme.com", "A"): (Rcode.noerror, ("192.0.2.10",)),
    ("www.acme.com", "AAAA"): (Rcode.noerror, ()),
    ("www.acme.com", "CNAME"): (Rcode.noerror, ()),
    ("acme.com", "A"): (Rcode.noerror, ("192.0.2.10",)),
    ("acme.com", "AAAA"): (Rcode.noerror, ()),
    ("acme.com", "NS"): (Rcode.noerror, ("ns1.example.net.",)),
    ("acme.com", "MX"): (Rcode.noerror, ("10 mail.acme.com.",)),
    ("acme.com", "TXT"): (Rcode.noerror, ("v=spf1 -all",)),
    ("_dmarc.acme.com", "TXT"): (Rcode.noerror, ("v=DMARC1; p=reject",)),
}


def value(
    changes: Mapping[tuple[str, str], tuple[Rcode, tuple[str, ...]]] | None = None,
    *,
    expires: datetime | None = None,
) -> dict[str, Any]:
    answers = {
        key: DnsAnswer(key[0], key[1], rcode, values, "192.0.2.53")
        for key, (rcode, values) in {**HEALTHY, **(changes or {})}.items()
    }
    rdap = None
    if expires is not None:
        rdap = {
            "source": "rdap",
            "source_url": "https://rdap.verisign.com/com/v1/domain/acme.com",
            "expires": expires.isoformat(),
        }
    return {
        "domain": TARGET.apex,
        "site_host": TARGET.site_host,
        "dns": dns_lookup.snapshot(TARGET, answers, checked_at=NOW),
        "rdap": rdap,
    }


def codes(
    stored: dict[str, Any] | None,
    *,
    status: AuditStatus = AuditStatus.done,
    site_answered: bool = False,
) -> list[str]:
    found = for_domain(
        stored, status=status, site_answered=site_answered, now=NOW, expiry_warn_days=60
    )
    return [finding.code for finding in found]


def test_a_healthy_domain_produces_nothing() -> None:
    assert codes(value(expires=NOW + timedelta(days=400))) == []


@pytest.mark.parametrize(
    "rcode", [Rcode.servfail, Rcode.refused, Rcode.timeout, Rcode.error, Rcode.other]
)
def test_an_unanswered_question_produces_nothing(rcode: Rcode) -> None:
    """Acceptance 1: only `absent` may become a finding."""
    unanswered = {
        ("www.acme.com", "A"): (rcode, ()),
        ("www.acme.com", "AAAA"): (rcode, ()),
        ("www.acme.com", "CNAME"): (rcode, ()),
        ("acme.com", "MX"): (rcode, ()),
        ("acme.com", "TXT"): (rcode, ()),
        ("_dmarc.acme.com", "TXT"): (rcode, ()),
    }
    assert codes(value(unanswered)) == []


def test_nxdomain_on_the_apex_is_unknown_and_produces_nothing() -> None:
    nx = dict.fromkeys(HEALTHY, (Rcode.nxdomain, ()))
    assert codes(value(nx)) == []


def test_nxdomain_on_dmarc_below_an_answering_apex_is_no_dmarc() -> None:
    assert codes(value({("_dmarc.acme.com", "TXT"): (Rcode.nxdomain, ())})) == ["no_dmarc"]


def test_a_null_mx_is_an_answer_not_an_absence() -> None:
    """RFC 7505 `0 .` says the domain takes no mail; it is a record, so no finding."""
    assert codes(value({("acme.com", "MX"): (Rcode.noerror, ("0 .",))})) == []


def test_two_spf_records_one_of_them_plus_all_is_only_multiple() -> None:
    both = {("acme.com", "TXT"): (Rcode.noerror, ("v=spf1 -all", "v=spf1 +all"))}
    assert codes(value(both)) == ["multiple_spf_records"]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ("v=spf1 include:x.com +all", ["spf_allows_all"]),
        ("v=spf1 include:x.com all", ["spf_allows_all"]),
        ("v=spf1 include:x.com ~all", []),
        ("v=spf1 include:x.com ?all", []),
        ("v=spf1 include:x.com -all", []),
    ],
)
def test_spf_all_mechanisms(record: str, expected: list[str]) -> None:
    assert codes(value({("acme.com", "TXT"): (Rcode.noerror, (record,))})) == expected


def test_other_txt_records_with_no_spf_is_no_spf() -> None:
    other = {("acme.com", "TXT"): (Rcode.noerror, ("google-site-verification=abc",))}
    assert codes(value(other)) == ["no_spf"]


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        (("v=DMARC1; p=none",), ["dmarc_policy_none"]),
        (("v=DMARC1; p=quarantine",), []),
        (("v=DMARC1; p=reject",), []),
        # Two DMARC records is an invalid setup; no policy can be read from it with certainty.
        (("v=DMARC1; p=none", "v=DMARC1; p=reject"), []),
    ],
)
def test_dmarc_policies(records: tuple[str, ...], expected: list[str]) -> None:
    assert codes(value({("_dmarc.acme.com", "TXT"): (Rcode.noerror, records)})) == expected


def test_the_dmarc_evidence_never_carries_a_report_address() -> None:
    stored = value(
        {("_dmarc.acme.com", "TXT"): (Rcode.noerror, ("v=DMARC1; p=none; rua=mailto:x@y.com",))}
    )
    [finding] = for_domain(
        stored, status=AuditStatus.done, site_answered=True, now=NOW, expiry_warn_days=60
    )
    assert finding.evidence_text is not None and "mailto" not in finding.evidence_text
    assert "v=DMARC1; p=none" in finding.evidence_text


def test_no_address_record_needs_all_three_absent_and_no_answer() -> None:
    gone = {
        ("www.acme.com", "A"): (Rcode.noerror, ()),
        ("www.acme.com", "AAAA"): (Rcode.noerror, ()),
        ("www.acme.com", "CNAME"): (Rcode.noerror, ()),
    }
    assert codes(value(gone)) == ["domain_no_a_record"]
    assert codes(value(gone), site_answered=True) == []
    half = {**gone, ("www.acme.com", "AAAA"): (Rcode.servfail, ())}
    assert codes(value(half)) == []


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (-1, ["domain_expired"]),
        (0, ["domain_expired"]),
        (30, ["domain_expiring_soon"]),
        (60.5, ["domain_expiring_soon"]),
        (61, []),
    ],
)
def test_expiry_boundaries(days: float, expected: list[str]) -> None:
    assert codes(value(expires=NOW + timedelta(days=days))) == expected


def test_no_rdap_snapshot_produces_no_registry_finding() -> None:
    assert codes(value(expires=None)) == []


@pytest.mark.parametrize("status", sorted(set(AuditStatus) - DOMAIN_FINDING_STATUSES))
def test_statuses_without_domain_findings(status: AuditStatus) -> None:
    gone = {("acme.com", "MX"): (Rcode.noerror, ())}
    assert codes(value(gone, expires=NOW - timedelta(days=3)), status=status) == []


def test_a_skipped_lookup_produces_nothing() -> None:
    assert codes(None) == []
