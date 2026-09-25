"""The audit endpoints, the business-list filters and who may see what.

Audits are built here with a stubbed fetcher rather than by running a whole demo load: the
end-to-end path has its own test, and these are about the API around it.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.fetch_backends import BackendResponse, FetchRequest
from app.core.safe_fetch import SafeFetcher
from app.modules.audit_web import service
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.audit_web.psi import PsiResult
from app.modules.auth.models import Role, User
from app.modules.businesses.models import Business
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from tests.conftest import FakeClock, auth_headers, make_user

PAGE = (
    "<!doctype html><html><head>"
    '<meta name="viewport" content="width=device-width">'
    "<title>Wellington Plumbing</title>"
    "</head><body><h1>Plumbing</h1>"
    "<p>We fix leaks across the whole city, seven days a week, and we answer the phone.</p>"
    "<footer>&copy; 2026 Wellington</footer>"
    "</body></html>"
)


class ScriptedBackend:
    """Answers every URL with one page, and remembers what it was asked for."""

    resolves_dns = False

    def __init__(self, body: str = PAGE, *, robots: str = "User-agent: *\nDisallow:\n") -> None:
        self.body = body
        self.robots = robots
        self.calls: list[str] = []

    def handles(self, host: str) -> bool:
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        self.calls.append(request.url)
        if request.parts.path == "/robots.txt":
            return BackendResponse(
                status_code=200,
                headers={"content-type": "text/plain"},
                body=self.robots.encode(),
            )
        return BackendResponse(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=self.body.encode(),
        )


class ScriptedPsi:
    def __init__(self, score: int | None = 88) -> None:
        self.score = score

    def analyse(self, url: str) -> PsiResult:
        return PsiResult(performance_score=self.score, lcp_ms=1800, cls=0.02, tbt_ms=80)


def tools(body: str = PAGE, *, robots: str | None = None, score: int | None = 88) -> Any:
    clock = FakeClock()
    backend = ScriptedBackend(body, **({"robots": robots} if robots is not None else {}))
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
        psi=ScriptedPsi(score),
        settings=settings,
    )


def make_business(
    session: Session,
    *,
    name: str = "Wellington Plumbing",
    website: str | None = "https://wellington.invalid/",
    kind: WebsiteKind = WebsiteKind.own_site,
    industry: str = "plumbing",
    status: BusinessStatus = BusinessStatus.operational,
) -> Business:
    business = Business(
        display_name=name,
        industry=industry,
        website=website,
        domain="wellington.invalid" if website else None,
        website_kind=kind,
        business_status=status,
        city="Austin",
        state="TX",
    )
    session.add(business)
    session.flush()
    return business


@pytest.fixture
def audited(db: Session) -> Business:
    business = make_business(db)
    service.audit_business(db, business, tools=tools())
    db.commit()
    return business


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer)


@pytest.fixture
def tech_admin(db: Session) -> User:
    return make_user(db, Role.tech_admin)


@pytest.fixture
def crm_manager(db: Session) -> User:
    return make_user(db, Role.crm_manager)


# --- reading an audit ---------------------------------------------------------------------


def test_the_history_of_one_business_is_newest_first(
    client: TestClient, db: Session, admin_user: User, audited: Business
) -> None:
    service.audit_business(db, audited, tools=tools(score=30))
    db.commit()

    response = client.get(
        f"/api/v1/businesses/{audited.id}/audits", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["created_at"] >= items[1]["created_at"]
    assert items[0]["status"] == "done"
    assert "slow_mobile" in items[0]["finding_codes"]


def test_the_history_of_an_unknown_business_is_a_404(client: TestClient, admin_user: User) -> None:
    response = client.get(
        f"/api/v1/businesses/{uuid.uuid4()}/audits", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_one_audit_carries_its_checks_findings_and_pagespeed(
    client: TestClient, db: Session, admin_user: User, audited: Business
) -> None:
    audit = service.latest_audit(db, audited.id)
    assert audit is not None

    response = client.get(
        f"/api/v1/website-audits/{audit.id}", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["checks"]["title"]["value"] == "Wellington Plumbing"
    assert body["checks"]["title"]["evidence_url"] == "https://wellington.invalid/"
    assert body["psi"]["performance_score"] == 88
    assert body["rules_version"] == "audit-3"
    assert {f["code"] for f in body["findings"]} == {
        "missing_meta_description",
        "no_structured_data",
        "no_contact_on_homepage",
        "no_online_booking",
        "no_live_chat",
        "thin_content",
        "no_section_headings",
    }


def test_an_unknown_audit_is_a_404(client: TestClient, admin_user: User) -> None:
    response = client.get(
        f"/api/v1/website-audits/{uuid.uuid4()}", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 404


# --- who may see the page text ------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.admin, Role.reviewer, Role.tech_admin])
def test_the_roles_that_check_the_machines_work_can_read_the_page_text(
    client: TestClient, db: Session, audited: Business, role: Role
) -> None:
    user = make_user(db, role)
    audit = service.latest_audit(db, audited.id)
    assert audit is not None

    body = client.get(
        f"/api/v1/website-audits/{audit.id}", headers=auth_headers(client, user)
    ).json()

    assert body["page_text"], f"{role.value} must be able to read the stored page text"
    assert body["page_text_hidden"] is False


@pytest.mark.parametrize("role", [Role.sales_rep, Role.crm_manager])
def test_page_text_is_hidden_from_the_other_roles(
    client: TestClient, db: Session, audited: Business, role: Role
) -> None:
    user = make_user(db, role)
    audit = service.latest_audit(db, audited.id)
    assert audit is not None

    body = client.get(
        f"/api/v1/website-audits/{audit.id}", headers=auth_headers(client, user)
    ).json()

    assert body["page_text"] is None
    assert body["page_text_hidden"] is True, "hidden, not missing — the two differ"
    assert body["findings"], "everything else is still readable"


def test_reading_an_audit_needs_authentication(client: TestClient, audited: Business) -> None:
    assert client.get(f"/api/v1/businesses/{audited.id}/audits").status_code == 401


# --- asking for an audit ------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.admin, Role.tech_admin, Role.reviewer, Role.sales_rep])
def test_the_roles_that_work_a_lead_may_ask_for_a_fresh_audit(
    client: TestClient, db: Session, role: Role
) -> None:
    business = make_business(db)
    db.commit()
    user = make_user(db, role)

    response = client.post(
        f"/api/v1/businesses/{business.id}/audit", headers=auth_headers(client, user)
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "audit"
    assert body["status"] == "queued"


def test_a_crm_manager_may_not_ask_for_an_audit(
    client: TestClient, db: Session, crm_manager: User
) -> None:
    business = make_business(db)
    db.commit()

    response = client.post(
        f"/api/v1/businesses/{business.id}/audit", headers=auth_headers(client, crm_manager)
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_asking_twice_with_one_idempotency_key_queues_one_run(
    client: TestClient, db: Session, admin_user: User
) -> None:
    business = make_business(db)
    db.commit()
    headers = {**auth_headers(client, admin_user), "Idempotency-Key": "audit-once"}

    first = client.post(f"/api/v1/businesses/{business.id}/audit", headers=headers)
    second = client.post(f"/api/v1/businesses/{business.id}/audit", headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


def test_asking_to_audit_an_unknown_business_is_a_404(client: TestClient, admin_user: User) -> None:
    response = client.post(
        f"/api/v1/businesses/{uuid.uuid4()}/audit", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 404


def test_only_a_resolution_run_can_have_its_businesses_audited(
    client: TestClient, db: Session, tech_admin: User
) -> None:
    run = JobRun(kind="discovery", status=JobRunStatus.done)
    db.add(run)
    db.commit()

    response = client.post(f"/api/v1/jobs/{run.id}/audit", headers=auth_headers(client, tech_admin))

    assert response.status_code == 422
    assert response.json()["error"]["details"]["kind"] == "discovery"


def test_a_sales_rep_may_not_audit_a_whole_run(
    client: TestClient, db: Session, sales_user: User
) -> None:
    run = JobRun(
        kind="resolution", status=JobRunStatus.done, params={"parent_run_id": str(uuid.uuid4())}
    )
    db.add(run)
    db.commit()

    response = client.post(f"/api/v1/jobs/{run.id}/audit", headers=auth_headers(client, sales_user))

    assert response.status_code == 403


def test_auditing_a_resolution_run_queues_an_audit_run(
    client: TestClient, db: Session, tech_admin: User
) -> None:
    run = JobRun(
        kind="resolution", status=JobRunStatus.done, params={"parent_run_id": str(uuid.uuid4())}
    )
    db.add(run)
    db.commit()

    response = client.post(f"/api/v1/jobs/{run.id}/audit", headers=auth_headers(client, tech_admin))

    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "audit"
    queued = db.get(JobRun, uuid.UUID(response.json()["id"]))
    assert queued is not None
    assert queued.params == {"parent_run_id": str(run.id)}


# --- the business list --------------------------------------------------------------------


def audit_with(
    session: Session,
    business: Business,
    *,
    codes: list[str],
    status: AuditStatus = AuditStatus.done,
) -> WebsiteAudit:
    """A stored audit with exactly these finding codes, for the filter tests."""
    audit = WebsiteAudit(
        business_id=business.id,
        url_audited=business.website or "",
        status=status,
        findings=[
            {
                "code": code,
                "severity": "medium",
                "service_category": "seo",
                "message": f"Audit found {code}.",
                "evidence_text": "evidence",
                "evidence_url": business.website,
            }
            for code in codes
        ],
        rules_version="audit-2",
    )
    session.add(audit)
    session.flush()
    return audit


@pytest.fixture
def three_businesses(db: Session) -> Iterator[dict[str, Business]]:
    both = make_business(db, name="Both Gaps")
    one = make_business(db, name="One Gap")
    clean = make_business(db, name="All Good")
    audit_with(db, both, codes=["no_https", "no_online_booking"])
    audit_with(db, one, codes=["no_online_booking"])
    audit_with(db, clean, codes=[])
    db.commit()
    yield {"both": both, "one": one, "clean": clean}


def names(response: Any) -> set[str]:
    return {item["display_name"] for item in response.json()["items"]}


def test_two_finding_filters_combine_with_and(
    client: TestClient, admin_user: User, three_businesses: dict[str, Business]
) -> None:
    response = client.get(
        "/api/v1/businesses?finding=no_online_booking&finding=no_https",
        headers=auth_headers(client, admin_user),
    )

    assert response.status_code == 200, response.text
    assert names(response) == {"Both Gaps"}


def test_one_finding_filter_returns_everything_that_has_it(
    client: TestClient, admin_user: User, three_businesses: dict[str, Business]
) -> None:
    response = client.get(
        "/api/v1/businesses?finding=no_online_booking", headers=auth_headers(client, admin_user)
    )

    assert names(response) == {"Both Gaps", "One Gap"}


def test_an_unknown_finding_code_returns_nothing_rather_than_everything(
    client: TestClient, admin_user: User, three_businesses: dict[str, Business]
) -> None:
    response = client.get(
        "/api/v1/businesses?finding=not_a_real_code", headers=auth_headers(client, admin_user)
    )

    assert response.json()["items"] == []


def test_only_the_newest_audit_is_filtered_on(
    client: TestClient, db: Session, admin_user: User
) -> None:
    """A business whose site has been fixed since is no longer a match."""
    business = make_business(db, name="Fixed Since")
    old = audit_with(db, business, codes=["no_https"])
    old.created_at = datetime.now(UTC) - timedelta(days=10)
    db.flush()
    audit_with(db, business, codes=[])
    db.commit()

    matched = client.get(
        "/api/v1/businesses?finding=no_https", headers=auth_headers(client, admin_user)
    )

    assert matched.json()["items"] == []


def test_the_audit_status_filter_selects_on_the_newest_audit(
    client: TestClient, db: Session, admin_user: User, three_businesses: dict[str, Business]
) -> None:
    blocked = make_business(db, name="Asked Us To Stay Away")
    audit_with(db, blocked, codes=["robots_blocked"], status=AuditStatus.robots_blocked)
    db.commit()

    response = client.get(
        "/api/v1/businesses?audit_status=robots_blocked", headers=auth_headers(client, admin_user)
    )

    assert names(response) == {"Asked Us To Stay Away"}


def test_a_list_item_carries_its_latest_audit(
    client: TestClient, admin_user: User, three_businesses: dict[str, Business]
) -> None:
    response = client.get("/api/v1/businesses", headers=auth_headers(client, admin_user))

    items = {item["display_name"]: item["latest_audit"] for item in response.json()["items"]}
    assert items["Both Gaps"]["status"] == "done"
    assert sorted(items["Both Gaps"]["finding_codes"]) == ["no_https", "no_online_booking"]
    assert items["Both Gaps"]["audited_at"]
    assert items["All Good"]["finding_codes"] == []


def test_a_business_that_was_never_audited_has_no_latest_audit(
    client: TestClient, db: Session, admin_user: User
) -> None:
    make_business(db, name="Never Audited")
    db.commit()

    response = client.get("/api/v1/businesses", headers=auth_headers(client, admin_user))

    [item] = [i for i in response.json()["items"] if i["display_name"] == "Never Audited"]
    assert item["latest_audit"] is None


def test_the_detail_view_also_carries_the_latest_audit(
    client: TestClient, admin_user: User, audited: Business
) -> None:
    response = client.get(
        f"/api/v1/businesses/{audited.id}", headers=auth_headers(client, admin_user)
    )

    assert response.status_code == 200, response.text
    assert response.json()["latest_audit"]["status"] == "done"


def test_a_finding_filter_combines_with_the_other_filters(
    client: TestClient, db: Session, admin_user: User, three_businesses: dict[str, Business]
) -> None:
    response = client.get(
        "/api/v1/businesses?finding=no_online_booking&city=Austin&industry=plumbing",
        headers=auth_headers(client, admin_user),
    )

    assert names(response) == {"Both Gaps", "One Gap"}

    empty = client.get(
        "/api/v1/businesses?finding=no_online_booking&city=Dallas",
        headers=auth_headers(client, admin_user),
    )
    assert empty.json()["items"] == []


def test_the_openapi_document_describes_the_new_endpoints() -> None:
    from app.main import create_app

    get_settings.cache_clear()
    paths = create_app().openapi()["paths"]

    assert "/api/v1/businesses/{business_id}/audit" in paths
    assert "/api/v1/businesses/{business_id}/audits" in paths
    assert "/api/v1/jobs/{job_run_id}/audit" in paths
    assert "/api/v1/website-audits/{website_audit_id}" in paths


# --- v0.11.0: accessibility and best-practices scores ------------------------------------


class ThreeScorePsi:
    def analyse(self, url: str) -> PsiResult:
        return PsiResult(performance_score=61, accessibility_score=58, best_practices_score=67)


def test_accessibility_and_best_practices_are_stored_shown_and_reported(
    client: TestClient, db: Session, admin_user: User
) -> None:
    business = make_business(db, name="Scored Plumbing")
    audit_tools = tools()
    audit_tools = service.AuditTools(
        fetcher=audit_tools.fetcher, psi=ThreeScorePsi(), settings=audit_tools.settings
    )
    audit = service.audit_business(db, business, tools=audit_tools)
    db.commit()

    assert (audit.accessibility_score, audit.best_practices_score) == (58, 67)
    body = client.get(
        f"/api/v1/website-audits/{audit.id}", headers=auth_headers(client, admin_user)
    ).json()
    assert body["accessibility_score"] == 58
    assert body["best_practices_score"] == 67
    messages = {f["code"]: f["message"] for f in body["findings"]}
    assert messages["low_accessibility_score"] == "PageSpeed scored accessibility at 58 out of 100."
    assert messages["low_best_practices_score"] == (
        "PageSpeed scored best practices at 67 out of 100."
    )


def test_without_a_pagespeed_key_every_score_is_null_and_nothing_is_reported(
    client: TestClient, db: Session, admin_user: User
) -> None:
    from app.core.http import ApiHttpClient
    from app.modules.audit_web.psi import NetworkPageSpeedClient

    business = make_business(db, name="Keyless Plumbing")
    audit_tools = tools()
    keyless = NetworkPageSpeedClient(ApiHttpClient(source="pagespeed_insights"), api_key="")
    audit_tools = service.AuditTools(
        fetcher=audit_tools.fetcher, psi=keyless, settings=audit_tools.settings
    )
    audit = service.audit_business(db, business, tools=audit_tools)
    db.commit()

    body = client.get(
        f"/api/v1/website-audits/{audit.id}", headers=auth_headers(client, admin_user)
    ).json()
    assert body["psi"] is None
    assert body["accessibility_score"] is None
    assert body["best_practices_score"] is None
    assert "PAGESPEED_API_KEY" in body["checks"]["psi_error"]["value"]
    codes = {f["code"] for f in body["findings"]}
    assert not codes & {"slow_mobile", "low_accessibility_score", "low_best_practices_score"}


def test_few_reviews_cites_the_listing_the_count_came_from(db: Session) -> None:
    from app.modules.businesses.models import BusinessFieldValue
    from app.modules.discovery.models import DiscoveredRecord
    from app.modules.sources.models import Source, SourceKind

    business = make_business(db, name="Quiet Plumbing")
    business.user_rating_count = 11
    places = Source(name="google_places", kind=SourceKind.api, config={}, enabled=True)
    db.add(places)
    db.flush()
    now = datetime.now(UTC)
    record = DiscoveredRecord(
        source_id=places.id,
        source_record_id="ChIJquiet",
        source_url="https://www.google.com/maps/place/?q=place_id:ChIJquiet",
        raw_payload={"id": "ChIJquiet"},
        first_discovered_at=now,
        last_discovered_at=now,
        business_id=business.id,
    )
    db.add(record)
    db.flush()
    db.add(
        BusinessFieldValue(
            business_id=business.id,
            field="user_rating_count",
            value="11",
            source_id=places.id,
            discovered_record_id=record.id,
            observed_at=now,
        )
    )
    db.flush()

    audit = service.audit_business(db, business, tools=tools())

    [finding] = [f for f in audit.findings if f["code"] == "few_reviews"]
    assert finding["message"] == "Listing shows 11 reviews."
    assert finding["evidence_url"] == "https://www.google.com/maps/place/?q=place_id:ChIJquiet"


def test_a_business_with_no_website_still_gets_few_reviews(db: Session) -> None:
    business = make_business(db, name="Offline Plumbing", website=None, kind=WebsiteKind.none)
    business.user_rating_count = 3

    audit = service.audit_business(db, business, tools=tools())

    assert audit.status is AuditStatus.skipped
    assert set(audit.finding_codes) == {"no_website", "few_reviews"}
