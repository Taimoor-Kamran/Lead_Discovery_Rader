"""The demo websites, audited end to end, against `expected_audits.json`.

This is the acceptance test for the audit engine as a whole: `make load-demo-data` runs
discovery, resolution and then the audits, and every status and finding code below is what
a developer will actually see in `/docs` afterwards. Not one request leaves the machine —
every demo host is answered from `app/demo/sites/`.
"""

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.demo.loader import DemoLoadResult, audit_key, load_demo_data, run_pipeline
from app.modules.adapters import registry
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.audit_web.service import latest_audit
from app.modules.auth.models import Role
from app.modules.businesses.models import Business
from app.modules.discovery.models import ApiCall, DiscoveredRecord
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.normalization.schemas import BusinessStatus
from tests.conftest import make_user

EXPECTED = json.loads(
    (
        Path(__file__).resolve().parents[2] / "app" / "demo" / "sites" / "expected_audits.json"
    ).read_text(encoding="utf-8")
)
BY_DOMAIN: dict[str, dict[str, Any]] = EXPECTED["by_domain"]
BY_RECORD: dict[str, dict[str, Any]] = EXPECTED["by_record"]


@pytest.fixture
def development() -> Iterator[None]:
    """Run as a developer's machine would: the demo source and the fixtures both exist."""
    previous = {name: os.environ.get(name) for name in ("APP_ENV", "ENVIRONMENT")}
    os.environ.update({"APP_ENV": "development", "ENVIRONMENT": "development"})
    get_settings.cache_clear()
    registry.reload_builtins()
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        registry.reload_builtins()


@pytest.fixture
def audited(db: Session, development: None) -> DemoLoadResult:
    """The whole demo, discovered, resolved and audited, exactly as the CLI does it."""
    make_user(db, Role.admin)
    result = load_demo_data(db)
    pipeline = run_pipeline(result)

    assert pipeline.resolution_status is JobRunStatus.done
    assert pipeline.audit_run_id is not None, "a finished resolution run must queue its audits"
    assert pipeline.audit_status is JobRunStatus.done
    db.expire_all()
    return result


def audit_run(session: Session, result: DemoLoadResult) -> JobRun:
    run = session.scalars(
        select(JobRun).where(JobRun.idempotency_key == audit_key(result.resolution_run_id))
    ).one()
    return run


def audit_for_domain(session: Session, domain: str) -> WebsiteAudit:
    business = session.scalars(select(Business).where(Business.domain == domain)).one()
    audit = latest_audit(session, business.id)
    assert audit is not None, f"{domain} was never audited"
    return audit


def audit_for_record(session: Session, source_record_id: str) -> WebsiteAudit:
    record = session.scalars(
        select(DiscoveredRecord).where(DiscoveredRecord.source_record_id == source_record_id)
    ).one()
    assert record.business_id is not None
    audit = latest_audit(session, record.business_id)
    assert audit is not None, f"{source_record_id} was never audited"
    return audit


# --- the headline numbers ---------------------------------------------------------------


def test_the_audit_run_summary_matches_the_expected_file(
    db: Session, audited: DemoLoadResult
) -> None:
    assert audit_run(db, audited).result_summary == EXPECTED["run_summary"]


def test_every_expected_business_has_exactly_one_audit(
    db: Session, audited: DemoLoadResult
) -> None:
    audits = list(db.scalars(select(WebsiteAudit)))

    assert len(audits) == EXPECTED["businesses_audited"]
    assert len({a.business_id for a in audits}) == len(audits), "one audit each, not two"


def test_a_permanently_closed_business_is_not_audited(db: Session, audited: DemoLoadResult) -> None:
    closed = db.scalars(
        select(Business).where(Business.business_status == BusinessStatus.closed_permanently)
    ).one()

    assert latest_audit(db, closed.id) is None
    assert "demo-e8" in EXPECTED["not_audited"]


def test_no_audit_failed(db: Session, audited: DemoLoadResult) -> None:
    """A `failed` audit means a bug in our own code, never a bad website."""
    failed = [a for a in db.scalars(select(WebsiteAudit)) if a.status is AuditStatus.failed]

    assert failed == []


# --- every designed case ----------------------------------------------------------------


def observed(session: Session) -> dict[str, dict[str, Any]]:
    """Every audited demo domain, in the shape `expected_audits.json` writes them."""
    result: dict[str, dict[str, Any]] = {}
    for audit in session.scalars(select(WebsiteAudit)):
        business = session.get(Business, audit.business_id)
        assert business is not None
        if business.domain is None:
            continue
        result[business.domain] = {
            "status": audit.status.value,
            "findings": sorted(audit.finding_codes),
        }
    return result


def test_every_demo_site_produces_the_expected_status_and_findings(
    db: Session, audited: DemoLoadResult
) -> None:
    """One assertion over the whole set, so a diff shows exactly which case moved."""
    expected = {
        domain: {"status": case["status"], "findings": sorted(case["findings"])}
        for domain, case in BY_DOMAIN.items()
    }

    assert observed(db) == expected


def test_a_business_with_no_site_of_its_own_is_skipped_without_a_fetch(
    db: Session, audited: DemoLoadResult
) -> None:
    for record_id, expected in BY_RECORD.items():
        audit = audit_for_record(db, record_id)

        assert audit.status.value == expected["status"], expected["case"]
        assert sorted(audit.finding_codes) == sorted(expected["findings"]), expected["case"]
        assert audit.http_status is None
        assert audit.final_url is None
        assert audit.page_text is None, "nothing was fetched, so there is nothing to store"
        assert audit.checks == {}


def test_pagespeed_numbers_are_stored_for_every_site_that_was_read(
    db: Session, audited: DemoLoadResult
) -> None:
    for domain, case in BY_DOMAIN.items():
        if not case.get("psi_score"):
            continue
        audit = audit_for_domain(db, domain)

        assert audit.psi is not None, domain
        assert audit.psi["performance_score"] == case["psi_score"], domain
        assert audit.psi["strategy"] == "mobile"
        assert audit.psi["lcp_ms"] and audit.psi["tbt_ms"] is not None


def test_a_pagespeed_quota_error_leaves_the_audit_done_with_psi_null(
    db: Session, audited: DemoLoadResult
) -> None:
    [domain] = [d for d, v in BY_DOMAIN.items() if v.get("psi_error")]
    audit = audit_for_domain(db, domain)

    assert audit.status is AuditStatus.done
    assert audit.psi is None
    assert "Quota exceeded" in audit.checks["psi_error"]["value"]
    assert audit.checks["title"]["value"], "the rest of the audit still completed"


def test_a_robots_blocked_site_says_nothing_about_its_content(
    db: Session, audited: DemoLoadResult
) -> None:
    for domain, case in BY_DOMAIN.items():
        if case["status"] != "robots_blocked":
            continue
        audit = audit_for_domain(db, domain)

        assert audit.finding_codes == ["robots_blocked"], domain
        assert audit.checks == {}, "no page was read, so there is nothing to report about one"
        assert audit.page_text is None
        assert audit.html_sha256 is None
        assert audit.findings[0]["evidence_url"].endswith("/robots.txt")


def test_the_redirect_and_tls_cases(db: Session, audited: DemoLoadResult) -> None:
    redirected = audit_for_domain(db, "travisheightsplumbers.invalid")

    assert redirected.url_audited.startswith("http://")
    assert redirected.final_url is not None and redirected.final_url.startswith("https://")
    assert redirected.checks["http_redirects_to_https"]["value"] is True
    assert redirected.checks["redirect_chain"]["value"] == ["http://travisheightsplumbers.invalid/"]

    tls = audit_for_domain(db, "abcplumbing.invalid")

    assert tls.status is AuditStatus.unreachable
    assert tls.checks["tls_valid"]["value"] is False
    assert tls.finding_codes == ["tls_invalid"]


def test_the_tech_stack_is_recorded(db: Session, audited: DemoLoadResult) -> None:
    for domain, case in BY_DOMAIN.items():
        if not case.get("tech_stack"):
            continue
        audit = audit_for_domain(db, domain)

        for platform in case["tech_stack"]:
            assert platform in audit.tech_stack["platforms"], domain


# --- what every audit must carry ---------------------------------------------------------


def test_every_finding_carries_its_evidence(db: Session, audited: DemoLoadResult) -> None:
    """The rule the whole spec rests on: a finding a human cannot check is not a finding."""
    for audit in db.scalars(select(WebsiteAudit)):
        for finding in audit.findings:
            assert finding["evidence_text"], f"{finding['code']} on {audit.url_audited}"
            # `few_reviews` links to the listing's own page, and a demo listing has none
            # (`source_url` is null for the fixture source); a Places one always does.
            if finding["code"] not in ("no_website", "few_reviews"):
                assert finding["evidence_url"], f"{finding['code']} on {audit.url_audited}"
            assert finding["message"].startswith(
                ("Audit found", "Audit could not", "PageSpeed", "Listing shows")
            )


def test_every_audit_records_the_rules_version_it_was_produced_by(
    db: Session, audited: DemoLoadResult
) -> None:
    for audit in db.scalars(select(WebsiteAudit)):
        assert audit.rules_version == "audit-3"


def test_page_text_is_kept_only_for_a_page_that_was_read(
    db: Session, audited: DemoLoadResult
) -> None:
    for audit in db.scalars(select(WebsiteAudit)):
        if audit.status is not AuditStatus.done:
            assert audit.page_text is None, audit.url_audited
            continue
        assert audit.html_sha256, audit.url_audited
        if "js_shell_suspected" in audit.finding_codes:
            # A JavaScript shell has no visible text to keep, which is the finding.
            assert audit.page_text is None
            assert audit.content_expires_at is None
        else:
            assert audit.page_text, f"{audit.url_audited} was read but stored no text"
            assert audit.content_expires_at is not None


def test_the_demo_audits_make_no_metered_api_call(db: Session, audited: DemoLoadResult) -> None:
    """Everything is answered from disk, so no PageSpeed call is ever billed."""
    calls = list(db.scalars(select(ApiCall)))

    assert calls == []


def test_running_the_audits_again_changes_nothing_within_the_max_age(
    db: Session, audited: DemoLoadResult
) -> None:
    """Idempotent inside the window: a second pass finds nothing left to audit."""
    from app.workers import tasks

    before = len(list(db.scalars(select(WebsiteAudit))))
    run = audit_run(db, audited)
    second = uuid.UUID(str(run.id))
    db.commit()

    # A fresh run over the same resolution run, keyed differently so it is not deduplicated.
    from app.modules.audit_web.service import enqueue_audits_for_run

    again = enqueue_audits_for_run(
        db, audited.resolution_run_id, idempotency_key=f"audit-again:{second}"
    )
    assert tasks.execute_job_run(again.id) is JobRunStatus.done
    db.expire_all()

    assert len(list(db.scalars(select(WebsiteAudit)))) == before
    assert (db.get(JobRun, again.id) or run).progress_total == 0
