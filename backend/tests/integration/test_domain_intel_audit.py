"""Domain intelligence inside a real audit, against a real database (spec v0.13.0, task 4).

The homepage is served by a scripted backend, DNS by the fixture resolver, and RDAP through
`respx` from recorded answers — one of them the synthetic registrant-contacts answer, whose
fabricated contact values must appear nowhere in what is stored.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.dns import FixtureResolver
from app.core.fetch_backends import BackendResponse, FetchRequest
from app.core.rdap import RdapResult, network_rdap_client
from app.core.safe_fetch import SafeFetcher
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.audit_web.psi import PageSpeedUnavailableError
from app.modules.businesses.models import Business
from app.modules.domain_intel.models import DomainIntel
from app.modules.domain_intel.service import DomainTools
from app.modules.normalization.schemas import WebsiteKind
from tests.conftest import FakeClock, load_fixture
from tests.integration.test_website_audits_api import make_business
from tests.unit.test_rdap import CONTACT_VALUES

DOMAIN = "synthetic-contacts.com"
SITE = f"https://www.{DOMAIN}/"
RDAP_URL = f"https://rdap.verisign.com/com/v1/domain/{DOMAIN}"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PAGE = b"<html><head><title>Synthetic Plumbing</title></head><body><h1>Plumbing</h1></body></html>"


class ScriptedBackend:
    """robots.txt allows everything; the homepage answers `status`."""

    resolves_dns = False

    def __init__(self, status: int = 200) -> None:
        self.status = status

    def handles(self, host: str) -> bool:
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        if request.parts.path == "/robots.txt":
            return BackendResponse(
                status_code=200, headers={"content-type": "text/plain"}, body=b"User-agent: *\n"
            )
        return BackendResponse(
            status_code=self.status, headers={"content-type": "text/html"}, body=PAGE
        )


class NoPsi:
    def analyse(self, url: str) -> Any:
        raise PageSpeedUnavailableError("not in this test")


class CountingRdap:
    """Wraps a client and counts lookups, so a test can prove none were made."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def lookup(self, domain: str) -> RdapResult:
        self.calls.append(domain)
        return self.inner.lookup(domain)  # type: ignore[no-any-return]


def _ok(*values: str) -> dict[str, Any]:
    return {"rcode": "NOERROR", "values": list(values), "resolver": "192.0.2.53"}


def recorded_dns() -> dict[str, Any]:
    return {
        f"www.{DOMAIN}": {"A": _ok("192.0.2.10"), "AAAA": _ok(), "CNAME": _ok()},
        DOMAIN: {
            "A": _ok("192.0.2.10"),
            "AAAA": _ok(),
            "NS": _ok("ns1.domaincontrol.com.", "ns2.domaincontrol.com."),
            "MX": _ok("0 synthetic-contacts-com.mail.protection.outlook.com."),
            "TXT": _ok("v=spf1 include:spf.protection.outlook.com -all", "MS=ms12345678"),
        },
        f"_dmarc.{DOMAIN}": {
            "TXT": _ok("v=DMARC1; p=quarantine; rua=mailto:pat.fabricated@example.com")
        },
    }


def settings_for(**overrides: Any) -> Settings:
    return Settings(
        jwt_secret=SecretStr("x" * 40),
        environment="ci",
        audit_host_throttle_seconds=0.0,
        bot_contact="x",
        **overrides,
    )


def tools_for(
    settings: Settings,
    resolver: FixtureResolver,
    rdap: CountingRdap,
    status: int = 200,
) -> service.AuditTools:
    clock = FakeClock()
    return service.AuditTools(
        fetcher=SafeFetcher(
            redis=fakeredis.FakeStrictRedis(),
            backends=[ScriptedBackend(status)],
            settings=settings,
            clock=clock,
            sleeper=clock.sleep,
        ),
        psi=NoPsi(),
        settings=settings,
        domain=DomainTools(resolver=resolver, rdap=rdap),
    )


@pytest.fixture
def rdap(mock_http: respx.MockRouter) -> CountingRdap:
    mock_http.get(RDAP_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("rdap", "synthetic_registrant_contacts.json")
        )
    )
    clock = FakeClock()
    return CountingRdap(
        network_rdap_client(
            settings_for(), fakeredis.FakeStrictRedis(), clock=clock, sleeper=clock.sleep
        )
    )


@pytest.fixture
def business(db: Session) -> Business:
    return make_business(db, name="Synthetic Plumbing", website=SITE)


def _audit(
    db: Session, business: Business, tools: service.AuditTools, now: datetime = NOW
) -> WebsiteAudit:
    audit = service.audit_business(db, business, tools=tools, now=now)
    db.commit()
    return audit


def _stored_row(db: Session) -> DomainIntel:
    db.expire_all()
    row = db.scalar(select(DomainIntel).where(DomainIntel.domain == DOMAIN))
    assert row is not None
    return row


def test_no_registrant_value_reaches_the_stored_row_or_the_audit(
    db: Session, business: Business, rdap: CountingRdap
) -> None:
    """Acceptance 3, asserted against what is stored — not the parser's return value."""
    audit = _audit(db, business, tools_for(settings_for(), FixtureResolver(recorded_dns()), rdap))

    row = _stored_row(db)
    stored_audit = db.get(WebsiteAudit, audit.id)
    assert stored_audit is not None
    domain_check = stored_audit.checks["domain_intel"]
    for stored in (json.dumps(row.dns), json.dumps(row.rdap), json.dumps(domain_check)):
        for value in CONTACT_VALUES:
            assert value not in stored
        assert "mailto" not in stored and "MS=ms12345678" not in stored
    # And nowhere else in the audit either — the page checks never saw these values.
    for value in CONTACT_VALUES:
        assert value not in json.dumps(stored_audit.checks)
    assert row.rdap is not None and row.rdap["registrar"] == "Synthetic Registrar, LLC"


def test_the_whole_snapshot_is_copied_into_the_audit(
    db: Session, business: Business, rdap: CountingRdap
) -> None:
    audit = _audit(db, business, tools_for(settings_for(), FixtureResolver(recorded_dns()), rdap))

    row = _stored_row(db)
    value = audit.checks["domain_intel"]["value"]
    assert value["domain"] == DOMAIN and value["site_host"] == f"www.{DOMAIN}"
    assert value["dns"] == row.dns
    assert value["rdap"] == row.rdap
    assert value["dns"]["resolvers"] == ["192.0.2.53"], "acceptance 2: the resolver used"
    assert value["dns"]["providers"] == {"dns": ["GoDaddy"], "mail": ["Microsoft 365"]}
    assert value["rdap"]["source_url"] == RDAP_URL
    assert row.dns_checked_at == NOW and row.rdap_checked_at == NOW


def test_a_second_audit_inside_the_ttl_asks_nothing(
    db: Session, business: Business, rdap: CountingRdap
) -> None:
    """Acceptance 8: DNS queries only on the first run — and the audit still has it all."""
    resolver = FixtureResolver(recorded_dns())
    tools = tools_for(settings_for(), resolver, rdap)
    first = _audit(db, business, tools)
    asked = len(resolver.queries)
    assert asked == 9 and rdap.calls == [DOMAIN]

    second = _audit(db, business, tools, now=NOW + timedelta(days=6, hours=23))

    assert len(resolver.queries) == asked
    assert rdap.calls == [DOMAIN]
    value = second.checks["domain_intel"]["value"]
    assert value["dns_cached"] is True and value["rdap_cached"] is True
    assert value["dns"] == first.checks["domain_intel"]["value"]["dns"]
    assert value["rdap"] == first.checks["domain_intel"]["value"]["rdap"]


def test_each_half_is_asked_again_after_its_own_ttl(
    db: Session, business: Business, rdap: CountingRdap
) -> None:
    resolver = FixtureResolver(recorded_dns())
    tools = tools_for(settings_for(), resolver, rdap)
    _audit(db, business, tools)

    _audit(db, business, tools, now=NOW + timedelta(days=7, seconds=1))
    assert len(resolver.queries) == 18, "DNS is a week old: asked again"
    assert rdap.calls == [DOMAIN], "RDAP is good for thirty days"

    _audit(db, business, tools, now=NOW + timedelta(days=30, seconds=1))
    assert len(resolver.queries) == 27
    assert rdap.calls == [DOMAIN, DOMAIN]
    assert _stored_row(db).rdap_checked_at == NOW + timedelta(days=30, seconds=1)


def test_an_rdap_error_is_never_cached(
    db: Session, business: Business, mock_http: respx.MockRouter
) -> None:
    route = mock_http.get(RDAP_URL).mock(return_value=httpx.Response(503))
    clock = FakeClock()
    rdap = CountingRdap(
        network_rdap_client(
            settings_for(), fakeredis.FakeStrictRedis(), clock=clock, sleeper=clock.sleep
        )
    )
    tools = tools_for(settings_for(), FixtureResolver(recorded_dns()), rdap)

    audit = _audit(db, business, tools)
    assert audit.checks["domain_intel"]["value"]["rdap"] is None
    assert audit.checks["domain_intel"]["value"]["reasons"]["rdap"] == (
        "RDAP lookup failed (TransientError)"
    )
    assert _stored_row(db).rdap is None

    _audit(db, business, tools, now=NOW + timedelta(hours=1))
    assert route.call_count == 2


def test_an_all_unknown_dns_answer_is_used_but_not_cached(
    db: Session, business: Business, rdap: CountingRdap
) -> None:
    resolver = FixtureResolver({})  # nothing recorded: every question is unknown
    tools = tools_for(settings_for(), resolver, rdap)

    audit = _audit(db, business, tools)
    outcomes = {q["outcome"] for q in audit.checks["domain_intel"]["value"]["dns"]["queries"]}
    assert outcomes == {"unknown"}
    assert _stored_row(db).dns is None

    _audit(db, business, tools, now=NOW + timedelta(hours=1))
    assert len(resolver.queries) == 18


@pytest.mark.parametrize("status", [200, 403, 503])
def test_the_domain_step_runs_whatever_the_homepage_answered(
    db: Session, business: Business, rdap: CountingRdap, status: int
) -> None:
    audit = _audit(
        db, business, tools_for(settings_for(), FixtureResolver(recorded_dns()), rdap, status)
    )
    assert audit.status in {AuditStatus.done, AuditStatus.not_readable, AuditStatus.bot_challenge}
    assert audit.checks["domain_intel"]["value"]["dns"] is not None


def test_with_both_halves_off_the_audit_is_as_before(
    db: Session, business: Business, rdap: CountingRdap
) -> None:
    """Task 7 / acceptance 9: no key, no question, no call."""
    resolver = FixtureResolver(recorded_dns())
    settings = settings_for(dns_enabled=False, rdap_enabled=False)

    audit = _audit(db, business, tools_for(settings, resolver, rdap))

    assert "domain_intel" not in audit.checks
    assert resolver.queries == [] and rdap.calls == []
    assert db.scalar(select(DomainIntel)) is None


def test_one_half_off_is_recorded_as_off(
    db: Session, business: Business, rdap: CountingRdap
) -> None:
    resolver = FixtureResolver(recorded_dns())
    audit = _audit(db, business, tools_for(settings_for(rdap_enabled=False), resolver, rdap))

    value = audit.checks["domain_intel"]["value"]
    assert value["rdap"] is None and value["reasons"] == {"rdap": "RDAP_ENABLED is off"}
    assert rdap.calls == []
    assert len(resolver.queries) == 9


@pytest.mark.parametrize(
    ("website", "kind"),
    [
        ("https://acme-plumbing.wixsite.com/home", WebsiteKind.builder_subdomain),
        ("https://www.facebook.com/acmeplumbing", WebsiteKind.social_profile),
    ],
)
def test_shared_domains_ask_nothing(
    db: Session, rdap: CountingRdap, website: str, kind: WebsiteKind
) -> None:
    """Acceptance 5: zero DNS queries and zero RDAP calls."""
    business = make_business(db, name="Acme Plumbing", website=website, kind=kind)
    resolver = FixtureResolver(recorded_dns())

    audit = _audit(db, business, tools_for(settings_for(), resolver, rdap))

    assert resolver.queries == [] and rdap.calls == []
    assert audit.checks["domain_intel"]["value"] is None
    assert audit.checks["domain_intel"]["evidence_text"] is not None
