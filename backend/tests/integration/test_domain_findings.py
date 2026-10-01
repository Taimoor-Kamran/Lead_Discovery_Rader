"""Each of the nine domain findings, from its own fixture, through a real audit (v0.13.0).

Acceptance 7: every code has a fixture, and the fixture produces that code and no other
domain finding. The fixtures are hand-built from real answer shapes (`synthetic` in the
filename) until the smoke script has recorded real domains.
"""

import re
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from app.core.dns import FixtureResolver
from app.core.fetch_backends import BackendResponse, ConnectFailedError, FetchRequest
from app.core.rdap import network_rdap_client
from app.core.safe_fetch import SafeFetcher
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.domain_intel.evidence import DOMAIN_FINDINGS
from app.modules.domain_intel.service import DNS_FIXTURE_DIR, DomainTools
from tests.conftest import FakeClock, load_fixture
from tests.integration.test_audit_bot_challenge import ChallengingBackend
from tests.integration.test_domain_intel_audit import (
    CountingRdap,
    NoPsi,
    ScriptedBackend,
    _audit,
    settings_for,
)
from tests.integration.test_website_audits_api import make_business

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
VERISIGN = re.compile(r"https://rdap\.verisign\.com/com/v1/domain/(?P<domain>[^/]+)")
RDAP_CODES = {"domain_expired", "domain_expiring_soon"}


def domain_for(code: str) -> str:
    return "synthetic-" + code.replace("_", "-") + ".com"


def rdap_answer(request: httpx.Request, domain: str) -> httpx.Response:
    """respx hands the URL pattern's named group in as `domain`."""
    for code in RDAP_CODES:
        if domain == domain_for(code):
            return httpx.Response(200, json=load_fixture("rdap", f"synthetic_{code}.json"))
    return httpx.Response(200, json=load_fixture("rdap", "synthetic_healthy.json"))


@pytest.fixture
def rdap(mock_http: respx.MockRouter) -> CountingRdap:
    mock_http.get(VERISIGN).mock(side_effect=rdap_answer)
    clock = FakeClock()
    return CountingRdap(
        network_rdap_client(
            settings_for(), fakeredis.FakeStrictRedis(), clock=clock, sleeper=clock.sleep
        )
    )


class NoAnswer:
    """Neither robots.txt nor the homepage gives any HTTP answer."""

    resolves_dns = False

    def handles(self, host: str) -> bool:
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        raise ConnectFailedError("scripted: no answer")


def audit_of(
    db: Session, rdap: CountingRdap, code: str, *, backend: Any = None, **settings: Any
) -> WebsiteAudit:
    business = make_business(
        db, name="Synthetic Plumbing", website=f"https://www.{domain_for(code)}/"
    )
    config = settings_for(**settings)
    clock = FakeClock()
    tools = service.AuditTools(
        fetcher=SafeFetcher(
            redis=fakeredis.FakeStrictRedis(),
            backends=[backend or ScriptedBackend(200)],
            settings=config,
            clock=clock,
            sleeper=clock.sleep,
        ),
        psi=NoPsi(),
        settings=config,
        domain=DomainTools(resolver=FixtureResolver.from_directory(DNS_FIXTURE_DIR), rdap=rdap),
    )
    return _audit(db, business, tools, NOW)


def domain_codes(audit: WebsiteAudit) -> list[str]:
    return [code for code in audit.finding_codes if code in DOMAIN_FINDINGS]


@pytest.mark.parametrize("code", sorted(DOMAIN_FINDINGS - {"domain_no_a_record"}))
def test_each_fixture_produces_its_finding_and_no_other(
    db: Session, rdap: CountingRdap, code: str
) -> None:
    audit = audit_of(db, rdap, code)

    assert audit.status is AuditStatus.done
    assert domain_codes(audit) == [code]
    [stored] = [f for f in audit.findings if f["code"] == code]
    assert stored["method"] == "api"
    assert stored["evidence_url"] is None
    assert stored["evidence_text"].startswith("RDAP " if code in RDAP_CODES else "DNS ")
    assert stored["message"].startswith(("Registry records show", "DNS records show"))


def test_the_expiry_findings_carry_the_registry_date(db: Session, rdap: CountingRdap) -> None:
    expired = audit_of(db, rdap, "domain_expired")
    soon = audit_of(db, rdap, "domain_expiring_soon")

    [gone] = [f for f in expired.findings if f["code"] == "domain_expired"]
    assert gone["message"] == (
        "Registry records show the domain registration for synthetic-domain-expired.com "
        "expired on 2026-09-01."
    )
    assert "expiration event 2026-09-01T04:00:00+00:00" in gone["evidence_text"]
    [near] = [f for f in soon.findings if f["code"] == "domain_expiring_soon"]
    assert near["message"] == (
        "Registry records show the domain registration for synthetic-domain-expiring-soon.com "
        "expires on 2026-10-31, in 29 days."
    )


def test_expiring_soon_follows_the_warning_window(db: Session, rdap: CountingRdap) -> None:
    """29 days left: inside the default 60, outside a 20-day window."""
    audit = audit_of(db, rdap, "domain_expiring_soon", audit_domain_expiry_warn_days=20)
    assert domain_codes(audit) == []


def test_no_address_record_is_reported_when_the_site_gave_no_answer(
    db: Session, rdap: CountingRdap
) -> None:
    audit = audit_of(db, rdap, "domain_no_a_record", backend=NoAnswer())

    assert audit.http_status is None
    assert audit.status in {AuditStatus.unreachable, AuditStatus.not_readable}
    assert domain_codes(audit) == ["domain_no_a_record"]
    [stored] = [f for f in audit.findings if f["code"] == "domain_no_a_record"]
    assert stored["message"] == (
        "DNS records show no address record (A, AAAA or CNAME) for "
        "www.synthetic-domain-no-a-record.com."
    )
    assert "www.synthetic-domain-no-a-record.com A NOERROR" in stored["evidence_text"]


@pytest.mark.parametrize("homepage", [200, 403, 503])
def test_no_address_record_never_appears_beside_an_http_answer(
    db: Session, rdap: CountingRdap, homepage: int
) -> None:
    """Acceptance 6, and decision C10: any status line proves the host resolved."""
    audit = audit_of(db, rdap, "domain_no_a_record", backend=ScriptedBackend(homepage))

    assert audit.http_status == homepage
    assert "domain_no_a_record" not in audit.finding_codes


@pytest.mark.parametrize(
    ("backend", "status"),
    [
        (ChallengingBackend(403), AuditStatus.bot_challenge),
        (ScriptedBackend(503), AuditStatus.not_readable),
        (NoAnswer(), None),
    ],
)
def test_domain_findings_survive_a_page_that_could_not_be_read(
    db: Session, rdap: CountingRdap, backend: Any, status: AuditStatus | None
) -> None:
    """Task 5 / acceptance 4: the domain was looked up whatever the homepage did."""
    audit = audit_of(db, rdap, "domain_expired", backend=backend)

    if status is not None:
        assert audit.status is status
    assert audit.status is not AuditStatus.done
    assert domain_codes(audit) == ["domain_expired"]


def test_with_both_halves_off_no_domain_finding_appears(db: Session, rdap: CountingRdap) -> None:
    audit = audit_of(db, rdap, "domain_expired", dns_enabled=False, rdap_enabled=False)

    assert domain_codes(audit) == []
    assert rdap.calls == []
