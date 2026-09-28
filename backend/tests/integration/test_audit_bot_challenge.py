"""A bot-protection challenge is its own outcome (spec v0.12.0, item 3b).

The production canary audited four plumbers whose sites answered HTTP 403 with Cloudflare's
challenge page, and reported the challenge page's title, headings and text as findings about
the business. Here the same page is served by a scripted backend.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.fetch_backends import BackendResponse, FetchRequest
from app.core.safe_fetch import SafeFetcher
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus
from app.modules.businesses.models import Business
from app.modules.opportunities.rules import rule_opportunities
from tests.conftest import FakeClock
from tests.integration.test_website_audits_api import make_business

CHALLENGE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "pages"
    / "canary_cloudflare_just_a_moment.html"
).read_text(encoding="utf-8")


class ChallengingBackend:
    """robots.txt is fine; the homepage is Cloudflare's challenge, with `status`."""

    resolves_dns = False

    def __init__(self, status: int = 403) -> None:
        self.status = status
        self.calls: list[str] = []

    def handles(self, host: str) -> bool:
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        self.calls.append(request.parts.path or "/")
        if request.parts.path == "/robots.txt":
            return BackendResponse(
                status_code=200, headers={"content-type": "text/plain"}, body=b"User-agent: *\n"
            )
        return BackendResponse(
            status_code=self.status,
            headers={"content-type": "text/html; charset=UTF-8"},
            body=CHALLENGE.encode(),
        )


class CountingPsi:
    def __init__(self) -> None:
        self.calls = 0

    def analyse(self, url: str) -> Any:
        self.calls += 1
        raise AssertionError("PageSpeed must not be asked about a challenge page")


def tools_for(backend: ChallengingBackend, psi: CountingPsi) -> Any:
    clock = FakeClock()
    settings = Settings(
        jwt_secret=SecretStr("x" * 40),
        environment="ci",
        audit_host_throttle_seconds=0.0,
        bot_contact="x",
    )
    return service.AuditTools(
        fetcher=SafeFetcher(
            redis=fakeredis.FakeStrictRedis(),
            backends=[backend],
            settings=settings,
            clock=clock,
            sleeper=clock.sleep,
        ),
        psi=psi,
        settings=settings,
    )


@pytest.fixture
def business(db: Session) -> Business:
    business = make_business(db, name="AAA Auger Plumbing Services")
    # Few reviews: on a normal audit this alone is a finding. Here it must not be.
    business.user_rating_count = 3
    db.flush()
    return business


@pytest.mark.parametrize("status", [403, 503])
def test_a_challenge_page_is_its_own_status_with_no_findings(
    db: Session, business: Business, status: int
) -> None:
    psi = CountingPsi()

    audit = service.audit_business(db, business, tools=tools_for(ChallengingBackend(status), psi))

    assert audit.status is AuditStatus.bot_challenge
    assert audit.findings == [], "not even the listing's few_reviews"
    assert audit.http_status == status
    assert audit.checks["bot_challenge"]["value"] == "Cloudflare"
    assert "Just a moment..." in audit.checks["bot_challenge"]["evidence_text"]
    assert audit.page_text is None
    assert audit.psi is None and psi.calls == 0


def test_a_challenge_is_not_rechecked_as_if_nothing_had_answered(
    db: Session, business: Business
) -> None:
    backend = ChallengingBackend(503)

    service.audit_business(db, business, tools=tools_for(backend, CountingPsi()))

    assert backend.calls == ["/robots.txt", "/"]


def test_a_challenged_audit_becomes_no_opportunity(db: Session, business: Business) -> None:
    audit = service.audit_business(
        db, business, tools=tools_for(ChallengingBackend(), CountingPsi())
    )

    assert rule_opportunities(business, audit) == []


def test_a_challenged_business_waits_its_own_retry_schedule(
    db: Session, business: Business, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = service.audit_business(
        db, business, tools=tools_for(ChallengingBackend(), CountingPsi())
    )
    now = datetime.now(UTC)
    audit.created_at = now - timedelta(days=6)
    db.flush()
    assert not service.needs_audit(db, business, now=now)

    audit.created_at = now - timedelta(days=7, minutes=1)
    db.flush()
    assert service.needs_audit(db, business, now=now)

    monkeypatch.setenv("AUDIT_BOT_CHALLENGE_RETRY_DAYS", "30")
    get_settings.cache_clear()
    assert not service.needs_audit(db, business, now=now)
