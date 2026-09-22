"""The `classification` job with a scripted model: budget, drift, escalation, isolation."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.ai.budget import AIBudget
from app.modules.ai.client import LLMError, LLMRequest, LLMResult
from app.modules.ai.models import AIClassification
from app.modules.audit_web.models import AuditStatus, WebsiteAudit
from app.modules.businesses.models import Business
from app.modules.jobs.models import JobRun, JobRunStatus
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind
from app.modules.opportunities import service
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from app.modules.opportunities.service import CLASSIFICATION_JOB_KIND, ClassificationTools
from app.modules.opportunities.worker import run_classification
from app.workers import tasks
from tests.factories import check, finding

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
URL = "https://wellington.invalid/"
PAGE = (
    "Wellington Plumbing. Family-run and fully licensed. We fix leaks across the whole city, "
    "seven days a week. We are looking for a new website this year."
)


class ScriptedLLM:
    """Answers with the texts it was given, in order; the last one repeats."""

    provider = "fake"

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.answers) - 1)
        answer = self.answers[index]
        if isinstance(answer, Exception):
            raise answer
        return LLMResult(
            text=answer, model=request.model, tokens_in=100, tokens_out=20, latency_ms=5
        )


def answer(
    *opportunities: tuple[str, float, str, str | None],
    intent: str = "none_detected",
    matches: bool = True,
    summary: str = "A family-run plumber.",
) -> str:
    return json.dumps(
        {
            "business_summary": summary,
            "industry": "plumbing",
            "industry_matches_listing": matches,
            "opportunities": [
                {
                    "service": service_key,
                    "confidence": confidence,
                    "rationale": "Audit found it.",
                    "evidence": [{"finding_code": code, "quote": quote, "source_url": URL}],
                }
                for service_key, confidence, quote, code in opportunities
            ],
            "buying_intent": intent,
            "unknowns": [],
            "needs_human_review": True,
        }
    )


BOOKING_MESSAGE = "Audit found no online booking or scheduling link on the homepage."


def make_tools(*answers: str | Exception, **settings_overrides: Any) -> ClassificationTools:
    settings = Settings(
        environment="ci",
        ai_provider="fake",
        ai_triage_model="scripted-triage",
        ai_escalation_model="scripted-escalation",
        **settings_overrides,
    )
    return ClassificationTools(
        llm=ScriptedLLM(*answers),
        budget=AIBudget(fakeredis.FakeStrictRedis(), settings=settings),
        settings=settings,
        provider="fake",
    )


def make_business(session: Session, *, name: str = "Wellington Plumbing") -> Business:
    business = Business(
        display_name=name,
        industry="plumbing",
        website=URL,
        domain=f"{uuid.uuid4().hex[:6]}.wellington.invalid",
        website_kind=WebsiteKind.own_site,
        business_status=BusinessStatus.operational,
        city="Austin",
        state="TX",
        phone_e164="+15125550100",
    )
    session.add(business)
    session.flush()
    return business


def make_audit(
    session: Session,
    business: Business,
    *,
    job_run_id: uuid.UUID | None = None,
    findings: list[dict[str, Any]] | None = None,
    page_text: str | None = PAGE,
    status: AuditStatus = AuditStatus.done,
    created_at: datetime = NOW,
) -> WebsiteAudit:
    audit = WebsiteAudit(
        business_id=business.id,
        job_run_id=job_run_id,
        url_audited=URL,
        final_url=URL,
        status=status,
        checks={"parsed": check(True), "social_links": check(["facebook"])} if page_text else {},
        tech_stack={"platforms": []},
        findings=findings if findings is not None else [finding("no_online_booking", url=URL)],
        page_text=page_text,
        rules_version="audit-2",
        started_at=NOW,
        finished_at=NOW,
        created_at=created_at,
    )
    session.add(audit)
    session.flush()
    return audit


def opportunities_of(session: Session, business: Business) -> dict[str, Opportunity]:
    rows = session.scalars(select(Opportunity).where(Opportunity.business_id == business.id))
    return {row.service: row for row in rows}


def classifications_of(session: Session, business: Business) -> list[AIClassification]:
    return list(
        session.scalars(
            select(AIClassification)
            .where(AIClassification.business_id == business.id)
            .order_by(AIClassification.created_at, AIClassification.id)
        )
    )


# --- one business ----------------------------------------------------------------------


def test_ai_disabled_still_yields_the_rule_opportunities(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = ClassificationTools(llm=None, budget=None, settings=Settings(), provider="disabled")

    outcome = service.classify(db, business, tools=tools)

    assert {o.service for o in outcome.opportunities} == {"booking_setup"}
    assert outcome.created == 1
    [row] = classifications_of(db, business)
    assert row.status.value == "skipped_disabled"
    assert outcome.opportunities[0].ai_agrees is None


def test_schema_drift_is_retried_once_then_escalated_then_given_up(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools("nope", "still nope", "escalation nope", "and again")

    outcome = service.classify(db, business, tools=tools)

    assert [r.tier for r in tools.llm.requests] == [  # type: ignore[union-attr]
        "triage",
        "triage",
        "escalation",
        "escalation",
    ]
    rows = classifications_of(db, business)
    assert [(r.status.value, r.escalated) for r in rows] == [
        ("schema_invalid", False),
        ("schema_invalid", True),
    ]
    assert rows[1].raw_output == "and again"
    assert {o.service for o in outcome.opportunities} == {"booking_setup"}
    assert outcome.opportunities[0].source.value == "rules"
    assert outcome.ai is not None and outcome.ai.calls == 4


def test_an_unclear_answer_is_escalated_exactly_once(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(
        answer(("booking_setup", 0.5, BOOKING_MESSAGE, "no_online_booking")),
        answer(("booking_setup", 0.5, BOOKING_MESSAGE, "no_online_booking")),
    )

    outcome = service.classify(db, business, tools=tools)

    assert [r.tier for r in tools.llm.requests] == ["triage", "escalation"]  # type: ignore[union-attr]
    rows = classifications_of(db, business)
    assert [r.escalated for r in rows] == [False, True]
    assert outcome.ai is not None and outcome.ai.escalated is True
    assert outcome.ai.reasons == ("unclear_confidence",)


def test_a_reused_escalation_answer_counts_no_escalation(db: Session) -> None:
    """v0.7.0 carry-over: the second business gets the escalation answer from the cache,
    so no call was made and nothing is counted as an escalation — in the outcome, in the
    run summary and in `/ai/usage`."""
    from app.modules.ai.router import usage_for
    from app.modules.opportunities.schemas import ClassificationResultSummary
    from app.modules.opportunities.worker import _count

    business = make_business(db)
    make_audit(db, business)
    unclear = answer(("booking_setup", 0.5, BOOKING_MESSAGE, "no_online_booking"))
    tools = make_tools(unclear, unclear)

    outcome_one = service.classify(db, business, tools=tools)
    assert outcome_one.ai is not None and outcome_one.ai.escalated is True
    assert len(tools.llm.requests) == 2  # type: ignore[union-attr]

    # Same input, same models: both tiers are reused and nothing is called.
    outcome_two = service.classify(db, business, tools=tools)

    assert len(tools.llm.requests) == 2  # type: ignore[union-attr]
    assert outcome_two.ai is not None
    assert outcome_two.ai.calls == 0 and outcome_two.ai.reused == 2
    assert outcome_two.ai.escalated is False, "a reused answer made no call"
    rows = classifications_of(db, business)
    assert [(r.status.value, r.escalated) for r in rows] == [
        ("ok", False),
        ("ok", True),
        ("reused", False),
        ("reused", True),
    ]

    summary = ClassificationResultSummary()
    _count(summary, outcome_one)
    _count(summary, outcome_two)
    assert summary.ai_escalations == 1 and summary.ai_reused == 2 and summary.ai_calls == 2

    usage = usage_for(db, datetime.now(UTC).date())
    assert usage.calls == 2 and usage.escalations == 1 and usage.reused == 2


def test_a_clear_answer_is_never_escalated(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")))

    outcome = service.classify(db, business, tools=tools)

    assert [r.tier for r in tools.llm.requests] == ["triage"]  # type: ignore[union-attr]
    assert outcome.ai is not None and outcome.ai.escalated is False
    booking = opportunities_of(db, business)["booking_setup"]
    assert float(booking.confidence) == 0.75, "0.6 from the rule, raised by at most 0.15"


def test_escalation_can_be_switched_off(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(
        answer(("booking_setup", 0.5, BOOKING_MESSAGE, "no_online_booking")),
        ai_escalation_enabled=False,
    )

    service.classify(db, business, tools=tools)

    assert [r.tier for r in tools.llm.requests] == ["triage"]  # type: ignore[union-attr]


def test_a_provider_error_leaves_the_rules_standing(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(LLMError("OpenAI answered 500", retryable=True))

    outcome = service.classify(db, business, tools=tools)

    [row] = classifications_of(db, business)
    assert row.status.value == "error"
    assert "500" in (row.error or "")
    assert {o.service for o in outcome.opportunities} == {"booking_setup"}
    assert outcome.ai is not None and outcome.ai.errors == 1


def test_a_provider_400_stores_the_body_and_the_time_it_wasted(db: Session) -> None:
    """The production failure, on the row a human would read (spec v0.9.0).

    Fourteen of eighteen businesses stored "OpenAI answered 400: BadRequestError" and
    `latency_ms` 0. The body naming `temperature` was discarded by the client and the
    failure's own elapsed time never left it, so the row said neither what broke nor that
    anything had taken time. Both now reach the row.
    """
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(
        LLMError(
            "OpenAI answered 400: BadRequestError: Unsupported value: 'temperature' does "
            "not support 0 with this model. param=temperature code=unsupported_value",
            retryable=False,
            status_code=400,
            latency_ms=412,
        )
    )

    service.classify(db, business, tools=tools)

    [row] = classifications_of(db, business)
    assert row.status.value == "error"
    assert "param=temperature" in (row.error or ""), "the row must name what broke"
    assert "does not support 0" in (row.error or "")
    assert row.latency_ms == 412, "a failed call still took time"


def test_explicit_intent_with_a_real_quote_scores_intent(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(
        answer(
            ("website_design", 0.9, "We are looking for a new website this year", None),
            intent="explicit",
        )
    )

    service.classify(db, business, tools=tools)

    website = opportunities_of(db, business)["website_design"]
    assert website.source.value == "ai"
    assert float(website.confidence) == 0.6
    assert website.score_components["intent"] == 1.0


def test_the_per_run_cap_skips_ai_and_keeps_the_rules(db: Session) -> None:
    first, second = make_business(db, name="First"), make_business(db, name="Second")
    make_audit(db, first)
    make_audit(db, second)
    tools = make_tools(
        answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")),
        ai_max_calls_per_run=1,
    )

    service.classify(db, first, tools=tools)
    outcome = service.classify(db, second, tools=tools)

    [row] = classifications_of(db, second)
    assert row.status.value == "skipped_budget"
    assert "per-run cap" in (row.error or "")
    assert {o.service for o in outcome.opportunities} == {"booking_setup"}
    assert outcome.ai is not None and outcome.ai.skipped_budget == 1
    assert len(tools.llm.requests) == 1  # type: ignore[union-attr]


def test_the_same_input_is_reused_without_a_call(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")))

    first = service.classify(db, business, tools=tools)
    second = service.classify(db, business, tools=tools)

    assert len(tools.llm.requests) == 1  # type: ignore[union-attr]
    assert second.ai is not None and second.ai.calls == 0 and second.ai.reused == 1
    rows = classifications_of(db, business)
    assert [r.status.value for r in rows] == ["ok", "reused"]
    assert rows[1].output == rows[0].output
    assert (first.created, first.updated, second.created, second.updated) == (1, 0, 0, 1)
    assert len(opportunities_of(db, business)) == 1


def test_a_changed_page_is_not_reused(db: Session) -> None:
    business = make_business(db)
    make_audit(db, business)
    tools = make_tools(answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")))
    service.classify(db, business, tools=tools)

    make_audit(
        db, business, page_text=PAGE + " New paragraph.", created_at=NOW + timedelta(minutes=1)
    )
    service.classify(db, business, tools=tools)

    assert len(tools.llm.requests) == 2  # type: ignore[union-attr]


def test_robots_blocked_and_closed_businesses_get_nothing(db: Session) -> None:
    blocked = make_business(db, name="Blocked")
    make_audit(
        db,
        blocked,
        status=AuditStatus.robots_blocked,
        findings=[finding("robots_blocked", url=URL)],
        page_text=None,
    )
    closed = make_business(db, name="Closed")
    closed.business_status = BusinessStatus.closed_permanently
    make_audit(db, closed)
    tools = make_tools(answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")))

    for business in (blocked, closed):
        outcome = service.classify(db, business, tools=tools)
        assert outcome.opportunities == []
        assert outcome.ai is None
    assert tools.llm.requests == []  # type: ignore[union-attr]


# --- the run ---------------------------------------------------------------------------


def audit_run(session: Session, businesses: list[Business]) -> JobRun:
    """A finished audit run and one audit per business inside it."""
    run = JobRun(kind="audit", status=JobRunStatus.done)
    session.add(run)
    session.flush()
    for business in businesses:
        make_audit(session, business, job_run_id=run.id)
    session.commit()
    return run


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> ClassificationTools:
    tools = make_tools(answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")))
    monkeypatch.setattr(ClassificationTools, "build", classmethod(lambda cls, **_: tools))
    return tools


def test_a_finished_audit_run_queues_a_classification_run(db: Session, scripted: Any) -> None:
    run = audit_run(db, [make_business(db)])

    tasks.follow_up(db, run)
    db.commit()

    queued = db.scalars(
        select(JobRun).where(JobRun.idempotency_key == f"classification:{run.id}")
    ).one()
    assert queued.kind == CLASSIFICATION_JOB_KIND
    assert queued.params == {"parent_run_id": str(run.id)}


def test_the_run_classifies_every_audited_business_and_reports_counts(
    db: Session, scripted: Any
) -> None:
    businesses = [make_business(db, name=f"B{i}") for i in range(3)]
    parent = audit_run(db, businesses)
    run = service.enqueue_classification_for_run(db, parent.id)

    assert tasks.execute_job_run(run.id) is JobRunStatus.done
    db.expire_all()

    summary = (db.get(JobRun, run.id) or run).result_summary or {}
    assert set(summary) == {
        "businesses",
        "opportunities_created",
        "opportunities_updated",
        "ai_calls",
        "ai_escalations",
        "ai_reused",
        "ai_skipped_budget",
        "ai_errors",
        "est_cost_usd",
    }
    assert summary["businesses"] == 3
    assert summary["opportunities_created"] == 3
    for business in businesses:
        assert set(opportunities_of(db, business)) == {"booking_setup"}


def test_one_failing_business_never_fails_the_run(
    db: Session, scripted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    good, bad = make_business(db, name="Good"), make_business(db, name="Bad")
    parent = audit_run(db, [good, bad])
    original = service.classify

    def explode(session: Session, business: Business, **kwargs: Any) -> Any:
        if business.id == bad.id:
            raise RuntimeError("boom")
        return original(session, business, **kwargs)

    monkeypatch.setattr(service, "classify", explode)
    run = service.enqueue_classification_for_run(db, parent.id)

    assert tasks.execute_job_run(run.id) is JobRunStatus.done
    db.expire_all()

    summary = (db.get(JobRun, run.id) or run).result_summary or {}
    assert summary["businesses"] == 2
    assert summary["ai_errors"] == 1
    assert set(opportunities_of(db, good)) == {"booking_setup"}
    assert opportunities_of(db, bad) == {}


def test_a_rules_only_run_finishes_done_with_no_ai_calls(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AI_PROVIDER=disabled` is a supported configuration, not a degraded run (v0.10.0 §3).

    The run reaches `done`, the summary records `ai_calls 0`, and every business still gets
    the opportunities the deterministic rules found — with `source = rules`, which is what
    makes the review page's badge read *Rules* with nothing hidden or rewritten.
    """
    tools = ClassificationTools(llm=None, budget=None, settings=Settings(), provider="disabled")
    monkeypatch.setattr(ClassificationTools, "build", classmethod(lambda cls, **_: tools))
    businesses = [make_business(db, name=f"B{i}") for i in range(2)]
    parent = audit_run(db, businesses)
    run = service.enqueue_classification_for_run(db, parent.id)

    assert tasks.execute_job_run(run.id) is JobRunStatus.done
    db.expire_all()

    summary = (db.get(JobRun, run.id) or run).result_summary or {}
    assert summary["businesses"] == 2
    assert summary["ai_calls"] == 0
    assert summary["ai_errors"] == 0
    assert summary["opportunities_created"] == 2
    for business in businesses:
        rows = opportunities_of(db, business)
        assert set(rows) == {"booking_setup"}
        assert rows["booking_setup"].source is OpportunitySource.rules
        [classification] = classifications_of(db, business)
        assert classification.status.value == "skipped_disabled"


def test_exceeding_the_daily_budget_skips_ai_but_finishes_the_run(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = make_tools(
        answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")),
        ai_daily_budget_usd=0.5,
        ai_triage_price_in_per_m=1.0,
        ai_triage_price_out_per_m=1.0,
    )
    tools.provider = "openai"  # so the scripted answer is priced rather than free
    assert tools.budget is not None
    from decimal import Decimal

    tools.budget.record(Decimal("0.50"))
    monkeypatch.setattr(ClassificationTools, "build", classmethod(lambda cls, **_: tools))
    businesses = [make_business(db, name=f"B{i}") for i in range(2)]
    parent = audit_run(db, businesses)
    run = service.enqueue_classification_for_run(db, parent.id)

    assert tasks.execute_job_run(run.id) is JobRunStatus.done
    db.expire_all()

    summary = (db.get(JobRun, run.id) or run).result_summary or {}
    assert summary["ai_calls"] == 0
    assert summary["ai_skipped_budget"] == 2
    for business in businesses:
        assert set(opportunities_of(db, business)) == {"booking_setup"}
        [row] = classifications_of(db, business)
        assert row.status.value == "skipped_budget"
        assert "daily budget" in (row.error or "")


def test_running_twice_creates_no_duplicate_pending_opportunities(
    db: Session, scripted: Any
) -> None:
    business = make_business(db)
    parent = audit_run(db, [business])

    for key in ("one", "two"):
        run = service.enqueue_classification_for_run(db, parent.id, idempotency_key=key)
        assert tasks.execute_job_run(run.id) is JobRunStatus.done
    db.expire_all()

    rows = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
    assert len(rows) == 1
    assert rows[0].review_status is ReviewStatus.pending


def test_the_partial_unique_index_refuses_a_second_pending_row(db: Session) -> None:
    from decimal import Decimal

    from sqlalchemy.exc import IntegrityError

    business = make_business(db)
    for _ in range(2):
        db.add(
            Opportunity(
                business_id=business.id,
                service="seo_gbp",
                source="rules",
                confidence=Decimal("0.5"),
                score=Decimal("0.5"),
                scoring_version="scoring-1",
                review_status=ReviewStatus.pending,
            )
        )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a_classification_run_needs_an_audit_run(db: Session) -> None:
    from app.core.errors import ValidationFailedError

    other = JobRun(kind="resolution", status=JobRunStatus.done)
    db.add(other)
    db.flush()

    with pytest.raises(ValidationFailedError):
        service.enqueue_classification_for_run(db, other.id)


def test_the_single_business_run(db: Session, scripted: Any) -> None:
    business = make_business(db)
    make_audit(db, business)
    db.commit()
    run = service.enqueue_classification_for_business(db, business.id)

    assert run.kind == CLASSIFICATION_JOB_KIND
    assert tasks.execute_job_run(run.id) is JobRunStatus.done
    db.expire_all()
    assert set(opportunities_of(db, business)) == {"booking_setup"}
    assert (db.get(JobRun, run.id) or run).progress_total == 1


def test_run_classification_is_the_registered_handler() -> None:
    assert tasks.get_handler(CLASSIFICATION_JOB_KIND) is run_classification
    get_settings.cache_clear()
