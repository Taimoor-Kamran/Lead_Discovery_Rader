"""v0.12.1: a booking call to action is followed once, and only its target decides.

The markup below is the *shape* of the pages production turned up, not their content:
ATX Electrical Services' "Schedule Now" leads to a Gravity Forms contact page with no date
or time input; a salon's "Book Now" leads to a page with a date picker. Hosts are the
tests' own.
"""

from pathlib import Path
from typing import Any

import fakeredis
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.fetch_backends import BackendResponse, FetchRequest
from app.core.safe_fetch import SafeFetcher
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus
from app.modules.audit_web.psi import PageSpeedUnavailableError
from tests.conftest import FakeClock
from tests.integration.test_website_audits_api import make_business

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"
HOME = "https://wellington.invalid/"


def page(body: str) -> str:
    return (
        "<!doctype html><html><head><title>Wellington Electrical | Austin electricians"
        "</title></head><body><h1>Wellington Electrical</h1>"
        "<p>Licensed electricians serving Austin since 1998. Panel upgrades, rewiring and "
        "lighting for homes and businesses across the city.</p>"
        f"{body}</body></html>"
    )


GRAVITY_CONTACT = page(
    '<form id="gform_1" action="/contact/" method="post">'
    '<label for="input_1_1">Name</label><input name="input_1" id="input_1_1" type="text">'
    '<label for="input_1_2">Email</label><input name="input_2" id="input_1_2" type="email">'
    '<label for="input_1_3">Message</label><textarea name="input_3" id="input_1_3"></textarea>'
    '<input type="hidden" name="gform_submission_time" value="1">'
    '<input type="submit" id="gform_submit_button_1" value="Submit"></form>'
)
DATE_PICKER = page(
    '<form action="/book/"><input type="text" name="input_4" class="datepicker medium">'
    '<select name="slot"><option>9:00</option></select><input type="submit" value="Book">'
    "</form>"
)
DATE_INPUT = page('<form><input type="date" name="when"><input type="submit"></form>')
WIDGET = page('<script src="https://assets.calendly.com/assets/external/widget.js"></script>')


class SiteBackend:
    """Serves `pages` by host and path; robots.txt allows everything unless told not to."""

    resolves_dns = False

    def __init__(self, pages: dict[str, str], *, robots: str = "User-agent: *\n") -> None:
        self.pages = pages
        self.robots = robots
        self.calls: list[str] = []

    def handles(self, host: str) -> bool:
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        url = f"{request.parts.scheme}://{request.parts.netloc}{request.parts.path or '/'}"
        self.calls.append(url)
        if request.parts.path == "/robots.txt":
            return BackendResponse(
                status_code=200, headers={"content-type": "text/plain"}, body=self.robots.encode()
            )
        body = self.pages.get(url)
        if body is None:
            return BackendResponse(
                status_code=404, headers={"content-type": "text/html"}, body=b"<h1>Not found</h1>"
            )
        return BackendResponse(
            status_code=200,
            headers={"content-type": "text/html; charset=UTF-8"},
            body=body.encode(),
        )


class NoPsi:
    def analyse(self, url: str) -> Any:
        raise PageSpeedUnavailableError("not in tests")


def tools_for(backend: SiteBackend) -> Any:
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
        psi=NoPsi(),
        settings=settings,
    )


def audit_of(db: Session, pages: dict[str, str], **backend: Any) -> tuple[Any, SiteBackend]:
    site = SiteBackend(pages, **backend)
    business = make_business(db, industry="electrical")
    audit = service.audit_business(db, business, tools=tools_for(site))
    assert audit.status is AuditStatus.done
    return audit, site


def codes(audit: Any) -> set[str]:
    return set(audit.finding_codes)


def test_schedule_now_to_a_contact_form_is_no_online_booking_naming_cta_and_target(
    db: Session,
) -> None:
    """The ATX case: the client's own verification found no date or time on the target."""
    audit, site = audit_of(
        db,
        {
            HOME: page('<a class="button" href="/contact/">Schedule Now</a>'),
            f"{HOME}contact/": GRAVITY_CONTACT,
        },
    )

    booking = audit.checks["booking"]
    assert booking["value"] is False
    assert booking["method"] == "cta"
    assert booking["target_url"] == f"{HOME}contact/"
    assert "no_online_booking" in codes(audit)
    [finding] = [f for f in audit.findings if f["code"] == "no_online_booking"]
    assert "Schedule Now" in finding["evidence_text"]
    assert f"{HOME}contact/" in finding["evidence_text"]
    assert finding["message"] == "Audit found no visible online booking flow on the homepage."
    assert site.calls.count(f"{HOME}contact/") == 1, "one fetch of the target, no more"


def test_a_booking_path_is_followed_the_same_way(db: Session) -> None:
    audit, _ = audit_of(
        db,
        {
            HOME: page('<a href="/schedule/"><img src="/cta.png" alt=""></a>'),
            f"{HOME}schedule/": GRAVITY_CONTACT,
        },
    )

    assert audit.checks["booking"]["method"] == "path"
    assert "no_online_booking" in codes(audit)


@pytest.mark.parametrize("target", [DATE_PICKER, DATE_INPUT, WIDGET])
def test_book_now_to_a_real_booking_flow_is_verified_and_suppresses_the_finding(
    db: Session, target: str
) -> None:
    audit, _ = audit_of(
        db, {HOME: page('<a href="/book-online/">Book Now</a>'), f"{HOME}book-online/": target}
    )

    assert audit.checks["booking"]["method"] == "verified_target"
    assert audit.checks["booking"]["value"] == "link text: Book Now"
    assert "no_online_booking" not in codes(audit)


@pytest.mark.parametrize(
    ("pages", "robots", "why"),
    [
        ({HOME: page('<a href="/book/">Book Now</a>')}, "User-agent: *\n", "HTTP 404"),
        (
            {HOME: page('<a href="/book/">Book Now</a>'), f"{HOME}book/": DATE_INPUT},
            "User-agent: *\nDisallow: /book/\n",
            "disallows",
        ),
        ({HOME: page("<button>Book Now</button>")}, "User-agent: *\n", "no link"),
    ],
)
def test_a_target_that_cannot_be_checked_is_unverified_and_draws_no_finding(
    db: Session, pages: dict[str, str], robots: str, why: str
) -> None:
    """Certainty or silence."""
    audit, site = audit_of(db, pages, robots=robots)

    booking = audit.checks["booking"]
    assert booking["method"] == "unverified"
    assert booking["value"] is None
    assert why in booking["evidence_text"]
    assert "no_online_booking" not in codes(audit)
    assert f"{HOME}book/" not in site.calls or why != "disallows"


def test_a_fresha_link_suppresses_it_without_a_fetch(db: Session) -> None:
    fresha = "https://www.fresha.com/a/wellington-salon-austin-abc123"
    audit, site = audit_of(db, {HOME: page(f'<a href="{fresha}">Book Now</a>')})

    assert audit.checks["booking"]["method"] == "booking_host"
    assert audit.checks["booking"]["value"] == "Fresha"
    assert "no_online_booking" not in codes(audit)
    assert not any("fresha" in call for call in site.calls)


def test_a_calendly_widget_suppresses_it_without_a_fetch(db: Session) -> None:
    audit, site = audit_of(db, {HOME: WIDGET})

    assert audit.checks["booking"]["method"] == "widget"
    assert audit.checks["booking"]["value"] == "Calendly"
    assert "no_online_booking" not in codes(audit)
    assert [c for c in site.calls if not c.endswith("robots.txt")] == [HOME]


def test_request_a_quote_is_no_online_booking_with_no_fetch(db: Session) -> None:
    audit, site = audit_of(
        db,
        {HOME: page('<a href="/quote/">Request a Quote</a>'), f"{HOME}quote/": DATE_INPUT},
    )

    assert audit.checks["booking"]["value"] is False
    assert "method" not in audit.checks["booking"]
    assert "no_online_booking" in codes(audit)
    assert f"{HOME}quote/" not in site.calls


def test_no_fetch_outside_the_booking_industries(db: Session) -> None:
    site = SiteBackend(
        {HOME: page('<a href="/book/">Book Now</a>'), f"{HOME}book/": GRAVITY_CONTACT}
    )
    business = make_business(db, industry="law_firm")
    audit = service.audit_business(db, business, tools=tools_for(site))

    assert audit.checks["booking"]["method"] == "unverified"
    assert f"{HOME}book/" not in site.calls
    assert "no_online_booking" not in codes(audit)
