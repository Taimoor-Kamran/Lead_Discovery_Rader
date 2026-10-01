"""v0.13.0 amendment W: domain evidence is withdrawn only on a lookup that worked.

One audit carries two kinds of evidence. Page evidence is judged only by an audit that read
the page (v0.12.1, correction H); domain evidence only by the DNS or RDAP question behind
each cited domain finding, whatever the homepage status.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.dns import DnsAnswer, Rcode
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.domain_intel import dns_lookup
from app.modules.domain_intel.evidence import DOMAIN_FINDINGS, lookup_worked
from app.modules.domain_intel.selection import DomainTarget
from app.modules.opportunities import service
from app.modules.opportunities.backfill import find_orphans
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from tests.integration.test_classification_run import (
    NOW,
    make_audit,
    make_business,
    make_tools,
)

LATER = NOW + timedelta(days=1)
TARGET = DomainTarget(site_host="www.wellington.example", apex="wellington.example")
RDAP = {
    "source": "rdap",
    "url": "https://rdap.example/domain/wellington.example",
    "registrar": "Example Registrar",
    "created": "2015-01-01T00:00:00+00:00",
    "expires": "2027-06-01T00:00:00+00:00",
    "status": ["active"],
}


def dns_snapshot(*, failing: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    """Every question answered `NOERROR` with a record, except `failing`, which SERVFAIL."""
    answers = {}
    for name, rdtype in dns_lookup.questions(TARGET):
        if (name, rdtype) in failing:
            answers[(name, rdtype)] = DnsAnswer(name, rdtype, Rcode.servfail)
            continue
        value = "v=spf1 -all" if rdtype == "TXT" and name == TARGET.apex else "x"
        if name == dns_lookup.dmarc_name(TARGET.apex):
            value = "v=DMARC1; p=reject"
        answers[(name, rdtype)] = DnsAnswer(name, rdtype, Rcode.noerror, (value,), "127.0.0.11")
    return dns_lookup.snapshot(TARGET, answers, checked_at=NOW)


def domain_check(
    *, dns: dict[str, Any] | None = None, rdap: dict[str, Any] | None = None
) -> dict[str, Any]:
    value = {
        "domain": TARGET.apex,
        "site_host": TARGET.site_host,
        "dns": dns,
        "rdap": rdap,
        "dns_cached": False,
        "rdap_cached": False,
        "reasons": {},
    }
    return {"value": value, "evidence_text": None, "evidence_url": None}


def opportunity(business: Business, service_key: str, codes: list[str]) -> Opportunity:
    return Opportunity(
        business_id=business.id,
        service=service_key,
        source=OpportunitySource.rules,
        reason="",
        evidence=[{"finding_code": code, "text": "x"} for code in codes],
        confidence=Decimal("0.5"),
        score=Decimal("0.5"),
        score_components={},
        scoring_version="scoring-1",
        review_status=ReviewStatus.pending,
    )


def later_audit(
    db: Session,
    business: Business,
    status: AuditStatus,
    domain: dict[str, Any] | None,
    *,
    when: datetime = LATER,
) -> WebsiteAudit:
    """A newer audit with no findings at all, carrying `domain` as its domain check."""
    audit = make_audit(
        db,
        business,
        findings=[],
        page_text="Family-run plumbing." if status is AuditStatus.done else None,
        status=status,
        created_at=when,
    )
    audit.checks = {**(audit.checks or {}), **({"domain_intel": domain} if domain else {})}
    db.flush()
    return audit


def classify(db: Session, business: Business) -> int:
    outcome = service.classify(db, business, tools=make_tools("not json"), now=LATER)
    db.flush()
    return outcome.withdrawn


def seeded(db: Session, rows: dict[str, list[str]]) -> tuple[Business, dict[str, Opportunity]]:
    business = make_business(db)
    made = {
        codes_key: opportunity(business, f"svc_{i}", codes)
        for i, (codes_key, codes) in enumerate(rows.items())
    }
    db.add_all(made.values())
    db.flush()
    return business, made


@pytest.mark.parametrize(
    "status",
    [
        AuditStatus.bot_challenge,
        AuditStatus.not_readable,
        AuditStatus.unreachable,
        AuditStatus.failed,
        AuditStatus.done,
    ],
)
def test_a_working_lookup_withdraws_a_domain_only_row_whatever_the_homepage_did(
    db: Session, status: AuditStatus
) -> None:
    business, rows = seeded(db, {"dns": ["no_spf", "no_dmarc"], "rdap": ["domain_expired"]})
    audit = later_audit(db, business, status, domain_check(dns=dns_snapshot(), rdap=RDAP))

    assert classify(db, business) == 2
    for row in rows.values():
        assert row.withdrawn_at == LATER
        assert row.review_status is ReviewStatus.pending
        reason = row.withdrawn_reason or ""
        assert "findings_absent" in reason and str(audit.id) in reason


def test_a_bot_challenge_audit_still_cannot_withdraw_page_evidence(db: Session) -> None:
    """The lookup worked, but the page was never seen: a row citing it is not judged."""
    business, rows = seeded(
        db,
        {
            "page": ["no_online_booking"],
            "mixed": ["domain_expired", "no_online_booking"],
            "domain": ["no_spf"],
        },
    )
    later_audit(
        db, business, AuditStatus.bot_challenge, domain_check(dns=dns_snapshot(), rdap=RDAP)
    )

    assert classify(db, business) == 1
    assert rows["page"].withdrawn_at is None
    assert rows["mixed"].withdrawn_at is None
    assert rows["domain"].withdrawn_at == LATER


@pytest.mark.parametrize("status", [AuditStatus.bot_challenge, AuditStatus.done])
def test_a_lookup_that_did_not_work_withdraws_no_domain_row(
    db: Session, status: AuditStatus
) -> None:
    """SERVFAIL on the question behind a finding, a failed RDAP call, or no lookup at all."""
    business, rows = seeded(
        db,
        {
            "spf": ["no_spf"],
            "dmarc": ["dmarc_policy_none"],
            "mx": ["no_domain_mx"],
            "a": ["domain_no_a_record"],
            "rdap": ["domain_expiring_soon"],
        },
    )
    dns = dns_snapshot(
        failing=(
            (TARGET.apex, "TXT"),
            (dns_lookup.dmarc_name(TARGET.apex), "TXT"),
            (TARGET.apex, "MX"),
            (TARGET.site_host, "AAAA"),
        )
    )
    later_audit(db, business, status, domain_check(dns=dns, rdap=None))

    assert classify(db, business) == 0
    assert all(row.withdrawn_at is None for row in rows.values())


@pytest.mark.parametrize("status", [AuditStatus.bot_challenge, AuditStatus.robots_blocked])
def test_an_audit_with_no_domain_snapshot_withdraws_no_domain_row(
    db: Session, status: AuditStatus
) -> None:
    business, rows = seeded(db, {"domain": ["no_spf"]})
    skipped = {"value": None, "evidence_text": "audit status robots_blocked: no domain lookup"}
    later_audit(db, business, status, skipped if status is AuditStatus.robots_blocked else None)

    assert classify(db, business) == 0
    assert rows["domain"].withdrawn_at is None


def test_a_domain_finding_still_present_keeps_its_row(db: Session) -> None:
    business, rows = seeded(db, {"domain": ["no_spf"]})
    audit = later_audit(
        db, business, AuditStatus.bot_challenge, domain_check(dns=dns_snapshot(), rdap=RDAP)
    )
    audit.findings = [{"code": "no_spf", "severity": "low"}]
    db.flush()

    assert classify(db, business) == 0
    assert rows["domain"].withdrawn_at is None


def test_the_backfill_uses_the_same_judgement(db: Session) -> None:
    business, rows = seeded(db, {"page": ["no_online_booking"], "domain": ["no_spf"]})
    later_audit(
        db, business, AuditStatus.bot_challenge, domain_check(dns=dns_snapshot(), rdap=RDAP)
    )

    found = {o.opportunity_id: o for o in find_orphans(db, apply=True, now=LATER)}

    assert found[str(rows["domain"].id)].withdrawn is True
    assert found[str(rows["page"].id)].withdrawn is False
    assert found[str(rows["page"].id)].skipped_audit_status == "bot_challenge"
    assert rows["page"].withdrawn_at is None


def test_every_domain_code_maps_to_a_lookup() -> None:
    worked = {"domain_intel": domain_check(dns=dns_snapshot(), rdap=RDAP)}
    nothing = {"domain_intel": domain_check(dns=None, rdap=None)}
    assert len(DOMAIN_FINDINGS) == 9
    for code in DOMAIN_FINDINGS:
        assert lookup_worked(worked, code), code
        assert not lookup_worked(nothing, code), code
        assert not lookup_worked({}, code), code
    assert not lookup_worked(worked, "no_online_booking")
