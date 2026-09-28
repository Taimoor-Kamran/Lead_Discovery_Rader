"""Our network being down is not the prospect's site being down (spec v0.12.0, item 3a).

Found during v0.11.2: a laptop in Modern Standby drops its network mid-run, and every
homepage it was asking for was stored as `unreachable` — a high-severity finding, read out
to the business, about a site that was fine. These tests drive the audit with a backend
that "goes over the network" (it resolves DNS through an injected resolver, so no socket is
ever opened) and a connectivity probe that says whatever the test needs.
"""

from datetime import timedelta
from typing import Any

import fakeredis
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.connectivity import ProbeResult
from app.core.fetch_backends import (
    BackendResponse,
    ConnectFailedError,
    FetchError,
    FetchRequest,
    FetchTimeoutError,
    TlsVerificationError,
)
from app.core.safe_fetch import SafeFetcher
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus
from app.modules.businesses.models import Business
from tests.conftest import FakeClock
from tests.factories import make_audit
from tests.integration.test_website_audits_api import PAGE, ScriptedPsi, make_business

RECHECK_SECONDS = 7.0
PUBLIC_IP = "93.184.216.34"


class FlakyBackend:
    """A network backend whose first `failures[path]` requests for a path fail."""

    resolves_dns = True

    def __init__(
        self,
        *,
        failures: dict[str, int] | None = None,
        error: type[FetchError] = ConnectFailedError,
        status: int | None = None,
    ) -> None:
        self.failures = dict(failures or {})
        self.error = error
        self.status = status
        self.calls: list[str] = []

    def handles(self, host: str) -> bool:
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        path = request.parts.path or "/"
        self.calls.append(path)
        if self.failures.get(path, 0) > 0:
            self.failures[path] -= 1
            if self.status is not None:
                return BackendResponse(
                    status_code=self.status, headers={"content-type": "text/html"}
                )
            raise self.error(f"scripted {self.error.kind} failure")
        if path == "/robots.txt":
            return BackendResponse(
                status_code=200,
                headers={"content-type": "text/plain"},
                body=b"User-agent: *\nDisallow:\n",
            )
        return BackendResponse(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=PAGE.encode(),
        )


class ScriptedProbe:
    """Answers the connectivity question from a script, and counts how often it was asked."""

    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.calls = 0

    def check(self) -> ProbeResult:
        self.calls += 1
        online = self.answers.pop(0) if self.answers else True
        return ProbeResult(
            online=online,
            detail="a connection to www.googleapis.com:443 "
            + ("opened" if online else "failed (OSError)"),
        )


def tools_for(backend: FlakyBackend, probe: ScriptedProbe | None) -> tuple[Any, FakeClock]:
    clock = FakeClock()
    settings = Settings(
        jwt_secret=SecretStr("x" * 40),
        environment="ci",
        audit_host_throttle_seconds=0.0,
        audit_unreachable_recheck_seconds=RECHECK_SECONDS,
        bot_contact="x",
    )
    fetcher = SafeFetcher(
        redis=fakeredis.FakeStrictRedis(),
        backends=[backend],
        settings=settings,
        clock=clock,
        sleeper=clock.sleep,
        resolver=lambda host, port: [PUBLIC_IP],
    )
    return (
        service.AuditTools(
            fetcher=fetcher, psi=ScriptedPsi(), settings=settings, connectivity=probe
        ),
        clock,
    )


@pytest.fixture
def business(db: Session) -> Business:
    return make_business(db, website="https://wellington.example/")


def previously_unreachable(db: Session, business: Business) -> None:
    """A site is only `unreachable` the second time in a row it gives no page (run 4)."""
    db.add(make_audit(business, status=AuditStatus.unreachable))
    db.flush()


# --- acceptance ----------------------------------------------------------------------------


def test_a_homepage_that_fails_while_our_network_is_down_is_failed_not_unreachable(
    db: Session, business: Business
) -> None:
    backend = FlakyBackend(failures={"/": 1})
    probe = ScriptedProbe(False)
    audit_tools, clock = tools_for(backend, probe)

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.failed
    assert "unreachable" not in audit.finding_codes
    assert audit.checks["network_available"]["value"] is False
    assert "Our own network was unavailable" in audit.checks["error"]["value"]
    assert backend.calls == ["/robots.txt", "/"], "no second request while we are offline"
    assert clock.delays == []


def test_a_homepage_that_fails_once_and_loads_on_the_recheck_is_done(
    db: Session, business: Business
) -> None:
    backend = FlakyBackend(failures={"/": 1}, error=FetchTimeoutError)
    probe = ScriptedProbe(True)
    audit_tools, clock = tools_for(backend, probe)

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.done
    assert "unreachable" not in audit.finding_codes
    assert backend.calls == ["/robots.txt", "/", "/"]
    assert clock.delays == [RECHECK_SECONDS]


def test_a_homepage_that_fails_twice_while_we_are_online_is_unreachable(
    db: Session, business: Business
) -> None:
    previously_unreachable(db, business)
    backend = FlakyBackend(failures={"/": 2})
    probe = ScriptedProbe(True, True)
    audit_tools, clock = tools_for(backend, probe)

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.unreachable
    assert audit.finding_codes == ["unreachable"]
    [finding] = audit.findings
    assert (
        "tried twice, 7 s apart, while our own connectivity check succeeded"
        in (finding["evidence_text"])
    )
    assert probe.calls == 2
    assert clock.delays == [RECHECK_SECONDS]


# --- the other paths -----------------------------------------------------------------------


def test_our_network_dropping_between_the_two_attempts_is_still_ours(
    db: Session, business: Business
) -> None:
    """Online at the first failure, offline at the second: the site was never fairly asked."""
    backend = FlakyBackend(failures={"/": 2})
    audit_tools, _ = tools_for(backend, ScriptedProbe(True, False))

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.failed
    assert audit.finding_codes == []


def test_robots_that_cannot_connect_while_we_are_offline_is_failed(
    db: Session, business: Business
) -> None:
    """The robots request is usually the first to hit an outage; it is judged the same way."""
    backend = FlakyBackend(failures={"/robots.txt": 1})
    audit_tools, _ = tools_for(backend, ScriptedProbe(False))

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.failed
    assert backend.calls == ["/robots.txt"], "the homepage is never asked for"


def test_robots_that_connects_on_the_recheck_goes_on_to_audit_the_page(
    db: Session, business: Business
) -> None:
    backend = FlakyBackend(failures={"/robots.txt": 1})
    audit_tools, _ = tools_for(backend, ScriptedProbe(True))

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.done
    assert backend.calls == ["/robots.txt", "/robots.txt", "/"]


def test_a_server_error_is_rechecked_before_it_is_reported(db: Session, business: Business) -> None:
    backend = FlakyBackend(failures={"/": 1}, status=503)
    audit_tools, _ = tools_for(backend, ScriptedProbe(True))

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.done


def test_a_certificate_failure_is_an_answer_and_is_not_rechecked(
    db: Session, business: Business
) -> None:
    backend = FlakyBackend(failures={"/robots.txt": 5}, error=TlsVerificationError)
    probe = ScriptedProbe()
    audit_tools, clock = tools_for(backend, probe)

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.finding_codes == ["tls_invalid"]
    assert probe.calls == 0
    assert clock.delays == []
    assert backend.calls == ["/robots.txt"]


def test_without_a_probe_a_failure_is_still_rechecked_but_never_blamed_on_us(
    db: Session, business: Business
) -> None:
    previously_unreachable(db, business)
    backend = FlakyBackend(failures={"/": 2})
    audit_tools, clock = tools_for(backend, None)

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.unreachable
    assert "our own connectivity was not checked" in audit.findings[0]["evidence_text"]
    assert clock.delays == [RECHECK_SECONDS]


def test_a_fixture_answered_from_disk_is_neither_probed_nor_rechecked(
    db: Session, business: Business
) -> None:
    """A demo site's failure is scripted: our network has nothing to do with it."""
    backend = FlakyBackend(failures={"/": 5})
    backend.resolves_dns = False
    probe = ScriptedProbe()
    audit_tools, clock = tools_for(backend, probe)

    audit = service.audit_business(db, business, tools=audit_tools)

    # A first audit: no answer is `not_readable` until a second audit also gets none.
    assert audit.status is AuditStatus.not_readable
    assert audit.findings == []
    assert probe.calls == 0
    assert clock.delays == []
    assert backend.calls == ["/robots.txt", "/"]


# --- unreachable only the second time in a row (v0.12.0, production run 4) ----------------


def test_a_site_that_loaded_last_time_and_gives_no_answer_now_is_not_readable(
    db: Session, business: Business
) -> None:
    """Mister Sparky of Austin: loaded twice in a day, then timed out twice."""
    db.add(make_audit(business, status=AuditStatus.done))
    db.flush()
    backend = FlakyBackend(failures={"/": 2}, error=FetchTimeoutError)
    audit_tools, _ = tools_for(backend, ScriptedProbe(True, True))

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.not_readable
    assert audit.findings == []
    assert "not as the site being offline" in audit.checks["not_readable"]["evidence_text"]


def test_a_site_that_could_not_be_loaded_last_time_either_is_unreachable(
    db: Session, business: Business
) -> None:
    """Grayzer Electric: no answer on every audit since 2026-09-23."""
    previously_unreachable(db, business)
    backend = FlakyBackend(failures={"/robots.txt": 2})
    audit_tools, _ = tools_for(backend, ScriptedProbe(True, True))

    audit = service.audit_business(db, business, tools=audit_tools)

    assert audit.status is AuditStatus.unreachable
    assert audit.finding_codes == ["unreachable"]


def test_our_own_failed_audit_in_between_is_looked_past(db: Session, business: Business) -> None:
    previously_unreachable(db, business)
    ours = make_audit(business, status=AuditStatus.failed)
    ours.created_at = ours.created_at + timedelta(days=1)  # newer than the unreachable one
    db.add(ours)
    db.flush()
    backend = FlakyBackend(failures={"/": 2})
    audit_tools, _ = tools_for(backend, ScriptedProbe(True, True))

    assert service.audit_business(db, business, tools=audit_tools).status is (
        AuditStatus.unreachable
    )


def test_two_unanswered_audits_in_a_row_make_the_second_unreachable(
    db: Session, business: Business
) -> None:
    first = service.audit_business(
        db, business, tools=tools_for(FlakyBackend(failures={"/": 2}), ScriptedProbe())[0]
    )
    db.flush()
    second = service.audit_business(
        db, business, tools=tools_for(FlakyBackend(failures={"/": 2}), ScriptedProbe())[0]
    )

    assert first.status is AuditStatus.not_readable
    assert second.status is AuditStatus.unreachable
