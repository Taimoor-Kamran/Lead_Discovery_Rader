"""The demo businesses, classified end to end, against `expected_opportunities.json`.

This is the acceptance test for v0.5.0 as a whole: `make load-demo-data` runs discovery,
resolution, the audits and then the classification, and every service, source, confidence
and AI status below is what a developer will see in `/docs` afterwards. The AI is the fake
provider answering from `app/demo/ai/`; not one request leaves the machine.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.demo.loader import DemoLoadResult, classification_key, load_demo_data, run_pipeline
from app.modules.adapters import registry
from app.modules.ai.models import AIClassification
from app.modules.auth.models import Role
from app.modules.businesses.models import Business
from app.modules.discovery.models import ApiCall, DiscoveredRecord
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.opportunities.models import Opportunity, ReviewStatus
from tests.conftest import make_user

DEMO_DIR = Path(__file__).resolve().parents[2] / "app" / "demo"
EXPECTED = json.loads((DEMO_DIR / "ai" / "expected_opportunities.json").read_text(encoding="utf-8"))


@pytest.fixture
def development() -> Iterator[None]:
    """Run as a developer's machine would: demo source, fixture sites and the fake AI."""
    import os

    previous = {name: os.environ.get(name) for name in ("APP_ENV", "ENVIRONMENT", "AI_PROVIDER")}
    os.environ.update({"APP_ENV": "development", "ENVIRONMENT": "development"})
    os.environ.pop("AI_PROVIDER", None)
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
def classified(db: Session, development: None) -> DemoLoadResult:
    """The whole demo, discovered, resolved, audited and classified, as the CLI does it."""
    make_user(db, Role.admin)
    result = load_demo_data(db)
    pipeline = run_pipeline(result)

    assert pipeline.audit_status is JobRunStatus.done
    assert pipeline.classification_run_id is not None, "a finished audit run must queue scoring"
    assert pipeline.classification_status is JobRunStatus.done
    db.expire_all()
    return result


def classification_run(session: Session, result: DemoLoadResult) -> JobRun:
    audit = session.scalars(
        select(JobRun).where(JobRun.idempotency_key == f"audit:{result.resolution_run_id}")
    ).one()
    return session.scalars(
        select(JobRun).where(JobRun.idempotency_key == classification_key(audit.id))
    ).one()


def business_key(session: Session, business: Business) -> str:
    """Domain where there is one, otherwise the demo record id, as the expected file is keyed."""
    if business.domain:
        return business.domain
    record = session.scalars(
        select(DiscoveredRecord).where(DiscoveredRecord.business_id == business.id)
    ).first()
    assert record is not None
    return record.source_record_id


def observed(session: Session) -> dict[str, dict[str, Any]]:
    """Every demo business, in the shape `expected_opportunities.json` writes them."""
    result: dict[str, dict[str, Any]] = {}
    for business in session.scalars(select(Business)):
        opportunities = list(
            session.scalars(
                select(Opportunity)
                .where(Opportunity.business_id == business.id)
                .order_by(Opportunity.service)
            )
        )
        rows = list(
            session.scalars(
                select(AIClassification)
                .where(AIClassification.business_id == business.id)
                .order_by(AIClassification.created_at, AIClassification.id)
            )
        )
        final = rows[-1] if rows else None
        result[business_key(session, business)] = {
            "opportunities": {
                o.service: {
                    "source": o.source.value,
                    "confidence": float(o.confidence),
                    "ai_agrees": o.ai_agrees,
                }
                for o in opportunities
            },
            "ai_statuses": [r.status.value for r in rows],
            "escalated": any(
                r.escalated and r.status.value not in ("skipped_budget",) for r in rows
            ),
            "buying_intent": (final.output or {}).get("buying_intent") if final else None,
            "rejected_rules": sorted({c["rule"] for r in rows for c in (r.rejected_claims or [])}),
        }
    return result


# --- the headline numbers ---------------------------------------------------------------


def test_the_classification_run_summary_matches_the_expected_file(
    db: Session, classified: DemoLoadResult
) -> None:
    assert classification_run(db, classified).result_summary == EXPECTED["run_summary"]


def test_every_demo_business_ends_up_exactly_as_expected(
    db: Session, classified: DemoLoadResult
) -> None:
    """One assertion over the whole set, so a diff shows exactly which case moved."""
    expected = {
        key: {
            "opportunities": {
                service: {
                    "source": spec["source"],
                    "confidence": spec["confidence"],
                    "ai_agrees": spec["ai_agrees"],
                }
                for service, spec in case["services"].items()
            },
            "ai_statuses": case["ai_statuses"],
            "escalated": case["escalated"],
            "buying_intent": case["buying_intent"],
            "rejected_rules": case["rejected_rules"],
        }
        for key, case in EXPECTED["businesses"].items()
    }
    actual = observed(db)
    for case in actual.values():
        for item in case["opportunities"].values():
            item["confidence"] = [item["confidence"], item["confidence"]]
    for key, case in expected.items():
        for service, item in case["opportunities"].items():
            low, high = item["confidence"]
            seen = actual[key]["opportunities"][service]["confidence"]
            assert low <= seen[0] <= high, (key, service, seen)
            item["confidence"] = seen

    assert actual == expected


def test_the_demo_makes_no_metered_api_call_and_costs_nothing(
    db: Session, classified: DemoLoadResult
) -> None:
    assert list(db.scalars(select(ApiCall))) == []
    for row in db.scalars(select(AIClassification)):
        assert row.est_cost_usd == 0
        assert row.model.startswith("fake-")
        assert row.prompt_version == "classify-1"


def test_everything_is_pending_and_nothing_is_approved(
    db: Session, classified: DemoLoadResult
) -> None:
    statuses = {o.review_status for o in db.scalars(select(Opportunity))}

    assert statuses == {ReviewStatus.pending}


# --- the designed cases ----------------------------------------------------------------


def opportunities_of(session: Session, domain: str) -> dict[str, Opportunity]:
    business = session.scalars(select(Business).where(Business.domain == domain)).one()
    return {
        o.service: o
        for o in session.scalars(select(Opportunity).where(Opportunity.business_id == business.id))
    }


def classifications_of(session: Session, domain: str) -> list[AIClassification]:
    business = session.scalars(select(Business).where(Business.domain == domain)).one()
    return list(
        session.scalars(
            select(AIClassification)
            .where(AIClassification.business_id == business.id)
            .order_by(AIClassification.created_at, AIClassification.id)
        )
    )


def test_every_opportunity_carries_evidence_with_text_and_url(
    db: Session, classified: DemoLoadResult
) -> None:
    """The rule the spec rests on: no evidence, no opportunity."""
    from app.modules.opportunities.catalogue import is_service

    rows = list(db.scalars(select(Opportunity)))
    assert rows
    for row in rows:
        assert is_service(row.service), row.service
        assert row.evidence, (row.service, row.business_id)
        for item in row.evidence:
            assert item["text"], item
            business = db.get(Business, row.business_id)
            assert business is not None
            # A demo listing has no source page, so its `few_reviews` evidence has no URL.
            if business.website_kind.value != "none" and item["finding_code"] != "few_reviews":
                assert item["url"], item
        assert set(row.score_components) == {"facts", "inference", "intent", "contactability"}
        assert row.scoring_version == "scoring-1"
        assert 0 <= float(row.score) <= 1


def test_an_invented_quote_is_dropped_and_recorded(db: Session, classified: DemoLoadResult) -> None:
    [row] = classifications_of(db, "oakhillplumbing.invalid")
    website = opportunities_of(db, "oakhillplumbing.invalid")["website_design"]

    assert row.status.value == "guardrail_trimmed"
    [claim] = [c for c in row.rejected_claims if c["rule"] == "quote_not_in_input"]
    assert claim["text"].startswith("We offer a 20% discount")
    assert not any("20% discount" in e["text"] for e in website.evidence)
    assert any(e["source"] == "ai" for e in website.evidence), "the valid quote stayed"


def test_an_ai_only_opportunity_is_capped_and_marked(
    db: Session, classified: DemoLoadResult
) -> None:
    ads = opportunities_of(db, "oakhillplumbing.invalid")["ads_social"]

    assert ads.source.value == "ai"
    assert float(ads.confidence) == 0.6
    assert ads.evidence[0]["text"] == "Find us on Facebook"


def test_an_invented_email_never_reaches_the_opportunity(
    db: Session, classified: DemoLoadResult
) -> None:
    [row] = classifications_of(db, "wixwaterworks.wixsite.com")
    booking = opportunities_of(db, "wixwaterworks.wixsite.com")["booking_setup"]

    assert "@" not in booking.reason
    assert "@" not in json.dumps(booking.evidence)
    assert "@" not in json.dumps(row.output)
    assert any(c["rule"] == "pii_in_rationale" for c in row.rejected_claims)
    assert "@" in (row.raw_output or ""), "the raw text is kept for the operator"


def test_the_prompt_injection_ends_with_no_intent(db: Session, classified: DemoLoadResult) -> None:
    [row] = classifications_of(db, "riversideplumbing.invalid")
    opportunities = opportunities_of(db, "riversideplumbing.invalid")

    assert row.output is not None
    assert row.output["buying_intent"] == "none_detected"
    assert row.output["needs_human_review"] is True
    assert json.loads(row.raw_output or "{}")["buying_intent"] == "explicit"
    for opportunity in opportunities.values():
        assert opportunity.score_components["intent"] == 0.0


def test_the_unclear_case_is_escalated_exactly_once_and_clear_cases_never(
    db: Session, classified: DemoLoadResult
) -> None:
    rows = classifications_of(db, "godaddygutters.godaddysites.com")

    assert [r.escalated for r in rows] == [False, True]
    assert rows[0].model == "fake-triage" and rows[1].model == "fake-escalation"
    assert "unclear_confidence" in (rows[1].error or "")
    website = opportunities_of(db, "godaddygutters.godaddysites.com")["website_design"]
    assert website.ai_classification_id == rows[1].id, "the escalation answer replaced triage"

    for domain in ("bartoncreekplumbing.invalid", "zilkerpipeworks.invalid"):
        assert [r.escalated for r in classifications_of(db, domain)] == [False]


def test_schema_drift_is_retried_once_within_the_same_classification(
    db: Session, classified: DemoLoadResult
) -> None:
    [row] = classifications_of(db, "zilkerpipeworks.invalid")

    assert row.status.value == "ok"
    assert row.tokens_out == 90 + 210, "both calls are metered on the one row"
    assert row.escalated is False


def test_the_ai_never_removes_a_rule_opportunity(db: Session, classified: DemoLoadResult) -> None:
    """The fake answer for Wix names booking_setup only; the other four rules stand."""
    opportunities = opportunities_of(db, "wixwaterworks.wixsite.com")

    assert set(opportunities) == {
        "website_design",
        "seo_gbp",
        "booking_setup",
        "ai_chat_setup",
        "ads_social",
    }
    assert opportunities["website_design"].ai_agrees is False
    assert opportunities["website_design"].source.value == "rules"


def test_a_business_without_page_text_gets_rules_only(
    db: Session, classified: DemoLoadResult
) -> None:
    assert classifications_of(db, "squaresitesewer.square.site") == []
    for opportunity in opportunities_of(db, "squaresitesewer.square.site").values():
        assert opportunity.ai_agrees is None
        assert opportunity.ai_classification_id is None


def test_the_ai_input_never_contains_a_phone_email_or_street_address(
    db: Session, classified: DemoLoadResult
) -> None:
    """Over every demo business, against the listing's own values and the PII patterns."""
    from app.core.config import get_settings
    from app.modules.ai import pii
    from app.modules.ai.prompt import build_input, render
    from app.modules.audit_web.service import latest_audit

    checked = 0
    for business in db.scalars(select(Business)):
        audit = latest_audit(db, business.id)
        if audit is None:
            continue
        built = build_input(business, audit, settings=get_settings())
        rendered = "\n".join(render(built)) + built.normalised()
        checked += 1
        if business.phone_e164:
            assert business.phone_e164 not in rendered
            assert business.phone_e164[-4:] not in built.page_text.replace("[phone removed]", "")
        if business.address_line1:
            assert business.address_line1 not in rendered
        if business.postal_code:
            assert business.postal_code not in rendered
        assert pii.find_phones(rendered) == []
        assert pii.find_emails(rendered) == []
        assert pii.find_addresses(rendered) == []
    assert checked >= 25


def test_classifying_again_makes_zero_ai_calls_and_no_duplicates(
    db: Session, classified: DemoLoadResult
) -> None:
    from app.modules.opportunities.service import enqueue_classification_for_run
    from app.workers import tasks

    before = len(list(db.scalars(select(Opportunity))))
    first = classification_run(db, classified)
    audit_run_id = first.params["parent_run_id"]  # type: ignore[index]
    db.commit()

    again = enqueue_classification_for_run(
        db, uuid_of(audit_run_id), idempotency_key=f"classification-again:{first.id}"
    )
    assert tasks.execute_job_run(again.id) is JobRunStatus.done
    db.expire_all()

    summary = (db.get(JobRun, again.id) or first).result_summary or {}
    assert summary["ai_calls"] == 0
    assert summary["ai_reused"] == EXPECTED["run_summary"]["ai_calls"] - 1, (
        "one reuse per business the AI answered; the escalation is reused too"
    )
    assert summary["opportunities_created"] == 0
    assert summary["opportunities_updated"] == before
    assert len(list(db.scalars(select(Opportunity)))) == before
    pending = [
        (o.business_id, o.service)
        for o in db.scalars(select(Opportunity)).all()
        if o.review_status is ReviewStatus.pending
    ]
    assert len(pending) == len(set(pending)), "one pending opportunity per business and service"
    reused = [r for r in db.scalars(select(AIClassification)) if r.status.value == "reused"]
    assert reused and all(r.tokens_in == 0 and r.est_cost_usd == 0 for r in reused)


@pytest.fixture
def classified_without_ai(db: Session, development: None) -> DemoLoadResult:
    """The same pipeline with AI disabled: every audited business must still be scored."""
    import os

    os.environ["AI_PROVIDER"] = "disabled"
    get_settings.cache_clear()
    try:
        make_user(db, Role.admin)
        result = load_demo_data(db)
        pipeline = run_pipeline(result)
        assert pipeline.classification_status is JobRunStatus.done
        db.expire_all()
        return result
    finally:
        os.environ.pop("AI_PROVIDER", None)
        get_settings.cache_clear()


def test_with_ai_disabled_every_audited_business_still_gets_its_rule_opportunities(
    db: Session, classified_without_ai: DemoLoadResult
) -> None:
    from app.modules.audit_web.models import AuditStatus
    from app.modules.audit_web.service import latest_audit
    from app.modules.normalization.schemas import BusinessStatus

    summary = classification_run(db, classified_without_ai).result_summary or {}
    assert summary["ai_calls"] == 0
    assert list(db.scalars(select(ApiCall))) == []
    rows = list(db.scalars(select(AIClassification)))
    assert rows and {r.status.value for r in rows} == {"skipped_disabled"}

    expected_rules = {
        key: {s: spec for s, spec in case["services"].items() if spec["source"] != "ai"}
        for key, case in EXPECTED["businesses"].items()
    }
    seen = 0
    for business in db.scalars(select(Business)):
        audit = latest_audit(db, business.id)
        key = business_key(db, business)
        actual = opportunities_by_service(db, business)
        if (
            audit is None
            or audit.status is AuditStatus.robots_blocked
            or business.business_status is BusinessStatus.closed_permanently
        ):
            assert actual == {}, key
            continue
        assert set(actual) == set(expected_rules[key]), key
        for service, opportunity in actual.items():
            assert opportunity.source.value == "rules", (key, service)
            assert opportunity.ai_agrees is None, (key, service)
            assert opportunity.evidence, (key, service)
        seen += 1
    assert seen >= 25


def opportunities_by_service(session: Session, business: Business) -> dict[str, Opportunity]:
    rows = session.scalars(select(Opportunity).where(Opportunity.business_id == business.id))
    return {row.service: row for row in rows}


def uuid_of(value: object) -> Any:
    import uuid

    return uuid.UUID(str(value))


# --- reset (what `make e2e` runs first) ----------------------------------------------------


def test_reset_demo_data_puts_every_opportunity_back_to_pending(
    db: Session, classified: DemoLoadResult
) -> None:
    from datetime import UTC, datetime

    from app.demo.loader import reset_demo_data
    from app.modules.auth.models import User
    from app.modules.compliance.models import Suppression
    from app.modules.review import service as review
    from app.modules.review.models import Decision, ReviewDecision
    from app.modules.review.schemas import ReviewRequest

    before = observed(db)
    reviewer = make_user(db, Role.reviewer, "reviewer@example.com")
    rep = make_user(db, Role.sales_rep, "rep1@example.com")
    pending = list(
        db.scalars(
            select(Opportunity)
            .where(Opportunity.review_status == ReviewStatus.pending)
            .order_by(Opportunity.score.desc(), Opportunity.id)
        )
    )
    assert len(pending) >= 3
    first, second = pending[0], next(o for o in pending if o.business_id != pending[0].business_id)
    now = datetime.now(UTC)
    review.decide(
        db,
        first.id,
        ReviewRequest(decision=Decision.approve, lock_version=0, assigned_to=rep.id),
        actor=reviewer,
        now=now,
    )
    review.decide(
        db,
        second.id,
        ReviewRequest(decision=Decision.do_not_contact, lock_version=0, note="E2E setup"),
        actor=reviewer,
        now=now,
    )
    db.commit()
    assert db.scalar(select(func.count()).select_from(ReviewDecision))
    assert db.scalar(select(func.count()).select_from(Suppression)) == 1
    assert db.scalar(
        select(func.count())
        .select_from(Opportunity)
        .where(Opportunity.review_status != ReviewStatus.pending)
    )

    result = reset_demo_data(db)
    db.expire_all()

    assert result.classification_status is JobRunStatus.done
    # Approve wrote one row; do-not-contact wrote one per open opportunity of its business.
    assert result.decisions_deleted >= 2
    assert result.suppressions_deleted == 1
    assert result.opportunities_deleted == len(before_opportunities(before))
    assert db.scalar(select(func.count()).select_from(ReviewDecision)) == 0
    assert db.scalar(select(func.count()).select_from(Suppression)) == 0
    statuses = set(db.scalars(select(Opportunity.review_status)))
    assert statuses == {ReviewStatus.pending}
    # The same opportunities as a fresh `make load-demo-data`: nothing lost, nothing new.
    # (The AI step records one more `reused` classification per business; that is the
    # provenance of the re-run, not a change in what the reviewer sees.)
    after = observed(db)
    assert {k: v["opportunities"] for k, v in after.items()} == {
        k: v["opportunities"] for k, v in before.items()
    }
    assert isinstance(db.get(User, reviewer.id), User)


def test_reset_demo_data_leaves_nothing_on_the_queue_for_a_second_executor(
    db: Session, classified: DemoLoadResult, fake_redis: Any
) -> None:
    """One run, one executor — the bug that broke `make e2e` (spec v0.9.0).

    `reset-demo-data` used to queue its classification run *and* execute it in this
    process. In the real stack a worker is listening, so it picked the same run up: the
    CLI took it to `running`, the worker found it already `running` and raised
    `InvalidStateTransitionError`, and `_fail_run` then wrote `failed` over the 56
    opportunities the CLI had already committed. The run must reach `done` with the
    summary it produced, and the queue must be empty so nothing can claim it twice.
    """
    from app.demo.loader import reset_demo_data

    fake_redis.delete("rq:queue:default")

    result = reset_demo_data(db)

    assert result.classification_status is JobRunStatus.done
    assert result.classification_summary, "a finished run reports what it did"
    assert result.classification_summary["businesses"] > 0
    assert result.classification_summary["ai_errors"] == 0

    db.expire_all()
    run = db.get(JobRun, result.classification_run_id)
    assert run is not None
    assert run.status is JobRunStatus.done
    assert run.result_summary is not None, "the summary must survive to the row"
    assert run.error is None
    assert fake_redis.llen("rq:queue:default") == 0, (
        "the run was handed to RQ as well as executed here; a worker would race it"
    )


def test_reset_demo_data_removes_the_e2e_users_and_nobody_else(
    db: Session, classified: DemoLoadResult
) -> None:
    """v0.8.0 review fix: `make e2e` signs in as `e2e-user@example.com` (any
    `e2e-*@example.com`). A reset deletes such a user when nothing references them;
    one that the append-only audit log or a RESTRICT foreign key still points at is
    deactivated instead, so history stays as written."""
    from app.demo.loader import reset_demo_data
    from app.modules.audit import service as audit
    from app.modules.audit.models import AuditLog
    from app.modules.auth.models import User
    from app.modules.jobs.models import SearchJob

    reviewer = make_user(db, Role.reviewer, "reviewer@example.com")
    untouched = make_user(db, Role.reviewer, "e2e-never-signed-in@example.com")
    signed_in = make_user(db, Role.reviewer, "E2E-USER@example.com")
    referenced = make_user(db, Role.reviewer, "e2e-1758380001@example.com")
    lookalike = make_user(db, Role.reviewer, "not-e2e-1758380002@example.com")
    # A sign-in names the user as actor; `audit_logs` is append-only, so the `SET NULL`
    # a delete would need is refused and the user can only be deactivated.
    audit.record(
        db,
        action="auth.login_succeeded",
        entity_type="user",
        entity_id=signed_in.id,
        actor_id=signed_in.id,
    )
    # A search job keeps its creator (ON DELETE RESTRICT): the same outcome.
    db.add(
        SearchJob(
            name="left behind by an e2e run",
            geo={"city": "Austin", "state": "TX"},
            industry="plumbing",
            source_ids=[],
            created_by=referenced.id,
        )
    )
    db.commit()
    untouched_id, signed_in_id, referenced_id = untouched.id, signed_in.id, referenced.id

    result = reset_demo_data(db)
    db.expire_all()

    assert result.classification_status is JobRunStatus.done
    assert result.e2e_users_deleted == 1
    assert result.e2e_users_deactivated == 2
    assert db.get(User, untouched_id) is None
    for user_id in (signed_in_id, referenced_id):
        kept = db.get(User, user_id)
        assert kept is not None and kept.is_active is False
    for user in (reviewer, lookalike):
        same = db.get(User, user.id)
        assert same is not None and same.is_active is True

    rows = list(db.scalars(select(AuditLog).order_by(AuditLog.id)))
    login = next(r for r in rows if r.action == "auth.login_succeeded")
    assert login.actor_id == signed_in_id, "the audit log is untouched"
    created = next(
        r for r in rows if r.action == "user.created" and r.entity_id == str(untouched_id)
    )
    assert created.after is not None
    assert created.after["email"] == "e2e-never-signed-in@example.com", "history stays"
    deleted = next(r for r in rows if r.action == "user.deleted")
    assert deleted.entity_id == str(untouched_id)
    assert deleted.before == {"email": "e2e-never-signed-in@example.com", "role": "reviewer"}
    deactivations = [
        r for r in rows if r.action == "user.updated" and r.before == {"is_active": True}
    ]
    assert {r.entity_id for r in deactivations} == {str(signed_in_id), str(referenced_id)}
    for row in deactivations:
        assert row.after is not None and row.after["is_active"] is False
        assert "e2e user" in row.after["reason"]
    by_job = next(r for r in deactivations if r.entity_id == str(referenced_id))
    assert by_job.after is not None
    assert by_job.after["refused_by"] == "fk_search_jobs_created_by_users"

    # Running it again: nothing to delete, the two inactive users are matched again and
    # stay exactly as they are (counted, not changed, `before` says already inactive).
    again = reset_demo_data(db)
    assert again.e2e_users_deleted == 0
    assert again.e2e_users_deactivated == 2
    db.expire_all()
    later = [
        r
        for r in db.scalars(select(AuditLog).order_by(AuditLog.id))
        if r.action == "user.updated" and r.before == {"is_active": False}
    ]
    assert len(later) == 2


def before_opportunities(snapshot: dict[str, dict[str, Any]]) -> list[str]:
    return [
        f"{key}:{service}" for key, item in snapshot.items() for service in item["opportunities"]
    ]
