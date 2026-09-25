"""The human review gate: decisions, locking, batch, undo, queue, detail, leads, RBAC."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import UserUpdate
from app.modules.auth.service import update_user
from app.modules.businesses.models import Business
from app.modules.compliance.models import Suppression
from app.modules.opportunities.models import Opportunity, OpportunitySource, ReviewStatus
from app.modules.review.models import ReviewDecision
from tests.conftest import auth_headers, make_user
from tests.integration.test_classification_run import make_audit, make_business

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


# --- helpers ----------------------------------------------------------------------------


def make_opportunity(
    session: Session,
    business: Business,
    service: str = "website_design",
    *,
    confidence: float = 0.8,
    score: float = 0.7,
    status: ReviewStatus = ReviewStatus.pending,
    source: OpportunitySource = OpportunitySource.rules,
    audit_id: uuid.UUID | None = None,
) -> Opportunity:
    row = Opportunity(
        business_id=business.id,
        website_audit_id=audit_id,
        service=service,
        source=source,
        reason=f"Audit found something for {service}.",
        evidence=[
            {
                "finding_code": "no_https",
                "text": "<script>alert(1)</script> served over http",
                "url": "https://wellington.invalid/",
                "source": "rules",
            }
        ],
        confidence=Decimal(str(confidence)),
        score=Decimal(str(score)),
        score_components={"facts": 0.5, "inference": 0.8, "intent": 0.0, "contactability": 1.0},
        scoring_version="scoring-1",
        review_status=status,
    )
    session.add(row)
    session.flush()
    return row


def review(
    client: TestClient,
    user: User,
    opportunity: Opportunity | uuid.UUID,
    decision: str,
    *,
    lock_version: int = 0,
    **fields: Any,
) -> Any:
    opportunity_id = opportunity.id if isinstance(opportunity, Opportunity) else opportunity
    return client.post(
        f"/api/v1/opportunities/{opportunity_id}/review",
        json={"decision": decision, "lock_version": lock_version, **fields},
        headers=auth_headers(client, user),
    )


def decisions_of(session: Session, opportunity: Opportunity) -> list[ReviewDecision]:
    session.expire_all()
    return list(
        session.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.opportunity_id == opportunity.id)
            .order_by(ReviewDecision.decided_at)
        )
    )


def audit_rows(session: Session, action: str) -> list[AuditLog]:
    session.expire_all()
    return list(session.scalars(select(AuditLog).where(AuditLog.action == action)))


@pytest.fixture
def reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer, "reviewer@example.com")


@pytest.fixture
def other_reviewer(db: Session) -> User:
    return make_user(db, Role.reviewer, "reviewer2@example.com")


@pytest.fixture
def rep(db: Session) -> User:
    return make_user(db, Role.sales_rep, "rep1@example.com")


@pytest.fixture
def crm_manager(db: Session) -> User:
    return make_user(db, Role.crm_manager)


@pytest.fixture
def tech_admin(db: Session) -> User:
    return make_user(db, Role.tech_admin)


@pytest.fixture
def business(db: Session) -> Business:
    row = make_business(db, name="Wellington Plumbing")
    make_audit(db, row)
    db.commit()
    return row


@pytest.fixture
def pending(db: Session, business: Business) -> Opportunity:
    row = make_opportunity(db, business)
    db.commit()
    return row


# --- each decision and its required fields --------------------------------------------


def test_approve_makes_a_lead_and_writes_history_and_audit(
    client: TestClient, db: Session, reviewer: User, rep: User, pending: Opportunity
) -> None:
    response = review(
        client, reviewer, pending, "approve", note="Looks right", assigned_to=str(rep.id)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_status"] == "approved"
    assert body["lock_version"] == 1
    assert body["assigned_to"] == str(rep.id)
    assert body["decided_by"] == str(reviewer.id)
    assert body["decision"]["decision"] == "approve"
    assert body["decision"]["from_status"] == "pending"
    assert body["decision"]["to_status"] == "approved"
    assert body["decision"]["can_undo"] is True
    assert body["decision"]["decided_by_email"] == "reviewer@example.com"
    assert body["decision"]["assigned_to_email"] == "rep1@example.com"

    [row] = decisions_of(db, pending)
    assert row.note == "Looks right" and row.assigned_to == rep.id
    [entry] = audit_rows(db, "opportunity.reviewed")
    assert entry.actor_id == reviewer.id
    assert entry.before == {
        "review_status": "pending",
        "lock_version": 0,
        "assigned_to": None,
        "decided_by": None,
        "decided_at": None,
    }
    assert entry.after is not None and entry.after["decision"] == "approve"


def test_approve_without_an_assignee_is_fine(
    client: TestClient, reviewer: User, pending: Opportunity
) -> None:
    response = review(client, reviewer, pending, "approve")
    assert response.status_code == 200, response.text
    assert response.json()["assigned_to"] is None


@pytest.mark.parametrize(
    ("assignee_role", "active"),
    [(Role.reviewer, True), (Role.crm_manager, True), (Role.sales_rep, False)],
)
def test_assigned_to_must_be_an_active_sales_rep(
    client: TestClient,
    db: Session,
    reviewer: User,
    pending: Opportunity,
    assignee_role: Role,
    active: bool,
) -> None:
    assignee = make_user(db, assignee_role)
    if not active:
        update_user(db, assignee.id, UserUpdate(is_active=False))
        db.commit()

    response = review(client, reviewer, pending, "approve", assigned_to=str(assignee.id))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field"] == "assigned_to"
    unknown = review(client, reviewer, pending, "approve", assigned_to=str(uuid.uuid4()))
    assert unknown.status_code == 422
    db.expire_all()
    assert db.get(Opportunity, pending.id).review_status is ReviewStatus.pending  # type: ignore[union-attr]


def test_reject_needs_a_known_reason_and_a_note_for_other(
    client: TestClient, db: Session, reviewer: User, pending: Opportunity
) -> None:
    missing = review(client, reviewer, pending, "reject")
    assert missing.status_code == 422
    assert missing.json()["error"]["details"]["allowed"] == [
        "evidence_wrong",
        "business_closed",
        "wrong_industry",
        "ai_mistake",
        "other",
    ]
    unknown = review(client, reviewer, pending, "reject", reason_code="too_small")
    assert unknown.status_code == 422
    other = review(client, reviewer, pending, "reject", reason_code="other")
    assert other.status_code == 422
    assert other.json()["error"]["details"]["field"] == "note"

    ok = review(client, reviewer, pending, "reject", reason_code="other", note="Site is fine")
    assert ok.status_code == 200, ok.text
    assert ok.json()["review_status"] == "rejected"
    [row] = decisions_of(db, pending)
    assert (row.reason_code, row.note) == ("other", "Site is fine")


def test_not_a_fit_has_its_own_reasons(
    client: TestClient, reviewer: User, pending: Opportunity
) -> None:
    wrong = review(client, reviewer, pending, "not_a_fit", reason_code="ai_mistake")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["details"]["allowed"] == [
        "too_small",
        "too_large",
        "outside_area",
        "already_client",
        "other",
    ]
    ok = review(client, reviewer, pending, "not_a_fit", reason_code="outside_area")
    assert ok.status_code == 200, ok.text
    assert ok.json()["review_status"] == "not_a_fit"
    assert ok.json()["decision"]["reason_code"] == "outside_area"


def test_needs_enrichment_needs_a_note_and_stays_decidable(
    client: TestClient, db: Session, reviewer: User, pending: Opportunity
) -> None:
    assert review(client, reviewer, pending, "needs_enrichment").status_code == 422

    first = review(client, reviewer, pending, "needs_enrichment", note="Check the phone")
    assert first.status_code == 200, first.text
    assert first.json()["review_status"] == "needs_enrichment"

    # Still open: it can be approved later, with the new lock version.
    second = review(client, reviewer, pending, "approve", lock_version=1)
    assert second.status_code == 200, second.text
    assert second.json()["review_status"] == "approved"
    assert [d.from_status for d in decisions_of(db, pending)] == [
        ReviewStatus.pending,
        ReviewStatus.needs_enrichment,
    ]


def test_duplicate_needs_another_opportunity_for_the_same_service(
    client: TestClient, db: Session, reviewer: User, business: Business, pending: Opportunity
) -> None:
    twin_business = make_business(db, name="Wellington Plumbing (dup)")
    twin = make_opportunity(db, twin_business, "website_design")
    other_service = make_opportunity(db, twin_business, "seo_gbp")
    db.commit()

    assert review(client, reviewer, pending, "duplicate").status_code == 422
    assert (
        review(client, reviewer, pending, "duplicate", duplicate_of=str(pending.id)).status_code
        == 422
    )
    assert (
        review(client, reviewer, pending, "duplicate", duplicate_of=str(uuid.uuid4())).status_code
        == 422
    )
    wrong = review(client, reviewer, pending, "duplicate", duplicate_of=str(other_service.id))
    assert wrong.status_code == 422
    assert wrong.json()["error"]["details"]["duplicate_of_service"] == "seo_gbp"

    ok = review(client, reviewer, pending, "duplicate", duplicate_of=str(twin.id))
    assert ok.status_code == 200, ok.text
    assert ok.json()["review_status"] == "duplicate"
    assert ok.json()["decision"]["duplicate_of"] == str(twin.id)


def test_do_not_contact_closes_every_opportunity_and_suppresses_the_business(
    client: TestClient, db: Session, reviewer: User, business: Business, pending: Opportunity
) -> None:
    approved = make_opportunity(db, business, "seo_gbp", status=ReviewStatus.approved)
    weak = make_opportunity(db, business, "ads_social", confidence=0.3)
    db.commit()

    assert review(client, reviewer, pending, "do_not_contact").status_code == 422

    response = review(client, reviewer, pending, "do_not_contact", note="Owner asked us not to")
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "do_not_contact"

    db.expire_all()
    for row in (pending, approved, weak):
        assert db.get(Opportunity, row.id).review_status is ReviewStatus.do_not_contact  # type: ignore[union-attr]
    assert len(audit_rows(db, "opportunity.reviewed")) == 3
    [suppression] = list(db.scalars(select(Suppression)))
    assert suppression.business_id == business.id
    assert suppression.domain == business.domain
    assert suppression.phone_e164 == business.phone_e164
    assert suppression.source.value == "review"
    assert suppression.reason == "Owner asked us not to"
    assert suppression.lifted_at is None
    assert len(audit_rows(db, "suppression.added")) == 1


# --- locking and state ----------------------------------------------------------------


def test_two_reviewers_the_second_gets_409(
    client: TestClient, reviewer: User, other_reviewer: User, pending: Opportunity
) -> None:
    first = review(client, reviewer, pending, "reject", reason_code="business_closed")
    assert first.status_code == 200

    second = review(client, other_reviewer, pending, "approve", lock_version=0)
    assert second.status_code == 409, second.text
    error = second.json()["error"]
    assert error["message"] == "Another reviewer already decided this"
    assert error["details"]["review_status"] == "rejected"


def test_a_stale_lock_version_on_an_open_row_is_409(
    client: TestClient, reviewer: User, pending: Opportunity
) -> None:
    response = review(client, reviewer, pending, "approve", lock_version=3)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_lock_version"
    assert response.json()["error"]["details"]["lock_version"] == 0


def test_a_decided_row_cannot_be_decided_again(
    client: TestClient, reviewer: User, pending: Opportunity
) -> None:
    assert review(client, reviewer, pending, "approve").status_code == 200
    again = review(client, reviewer, pending, "reject", lock_version=1, reason_code="ai_mistake")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_state_transition"


def test_unknown_opportunity_is_404(client: TestClient, reviewer: User) -> None:
    assert review(client, reviewer, uuid.uuid4(), "approve").status_code == 404


# --- batch --------------------------------------------------------------------------------


def batch(client: TestClient, user: User, **body: Any) -> Any:
    return client.post(
        "/api/v1/opportunities/review-batch", json=body, headers=auth_headers(client, user)
    )


def test_batch_rejects_with_per_id_results(
    client: TestClient, db: Session, reviewer: User, business: Business, pending: Opportunity
) -> None:
    second = make_opportunity(db, business, "seo_gbp")
    decided = make_opportunity(db, business, "booking_setup", status=ReviewStatus.approved)
    db.commit()
    missing = uuid.uuid4()

    response = batch(
        client,
        reviewer,
        ids=[str(pending.id), str(second.id), str(decided.id), str(missing), str(pending.id)],
        decision="reject",
        reason_code="evidence_wrong",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decided"] == 2
    assert [(i["id"], i["result"]) for i in body["items"]] == [
        (str(pending.id), "ok"),
        (str(second.id), "ok"),
        (str(decided.id), "conflict"),
        (str(missing), "not_allowed"),
    ]
    db.expire_all()
    assert db.get(Opportunity, pending.id).review_status is ReviewStatus.rejected  # type: ignore[union-attr]
    assert db.get(Opportunity, decided.id).review_status is ReviewStatus.approved  # type: ignore[union-attr]
    assert len(decisions_of(db, pending)) == 1
    assert len(audit_rows(db, "opportunity.reviewed")) == 2


def test_batch_not_a_fit_and_other_needs_a_note(
    client: TestClient, reviewer: User, pending: Opportunity
) -> None:
    bad = batch(client, reviewer, ids=[str(pending.id)], decision="not_a_fit", reason_code="other")
    assert bad.status_code == 422
    wrong = batch(
        client, reviewer, ids=[str(pending.id)], decision="not_a_fit", reason_code="ai_mistake"
    )
    assert wrong.status_code == 422
    ok = batch(
        client,
        reviewer,
        ids=[str(pending.id)],
        decision="not_a_fit",
        reason_code="other",
        note="Franchise",
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["items"][0]["review_status"] == "not_a_fit"


@pytest.mark.parametrize("decision", ["approve", "duplicate", "needs_enrichment", "do_not_contact"])
def test_nothing_but_reject_and_not_a_fit_can_be_batched(
    client: TestClient, db: Session, reviewer: User, pending: Opportunity, decision: str
) -> None:
    response = batch(
        client, reviewer, ids=[str(pending.id)], decision=decision, reason_code="other", note="x"
    )
    assert response.status_code == 422, response.text
    db.expire_all()
    assert db.get(Opportunity, pending.id).review_status is ReviewStatus.pending  # type: ignore[union-attr]


def test_batch_is_capped_at_fifty(client: TestClient, reviewer: User) -> None:
    too_many = batch(
        client,
        reviewer,
        ids=[str(uuid.uuid4()) for _ in range(51)],
        decision="reject",
        reason_code="ai_mistake",
    )
    assert too_many.status_code == 422
    empty = batch(client, reviewer, ids=[], decision="reject", reason_code="ai_mistake")
    assert empty.status_code == 422
    fifty = batch(
        client,
        reviewer,
        ids=[str(uuid.uuid4()) for _ in range(50)],
        decision="reject",
        reason_code="ai_mistake",
    )
    assert fifty.status_code == 200
    assert fifty.json()["decided"] == 0


# --- undo ---------------------------------------------------------------------------------


def undo(client: TestClient, user: User, decision_id: str) -> Any:
    return client.post(
        f"/api/v1/review-decisions/{decision_id}/undo", headers=auth_headers(client, user)
    )


def age_decision(session: Session, decision_id: str, minutes: int) -> None:
    row = session.get(ReviewDecision, uuid.UUID(decision_id))
    assert row is not None
    row.decided_at = row.decided_at - timedelta(minutes=minutes)
    session.commit()


def test_undo_restores_the_previous_status_within_the_window(
    client: TestClient, db: Session, reviewer: User, rep: User, pending: Opportunity
) -> None:
    decided = review(client, reviewer, pending, "approve", assigned_to=str(rep.id)).json()

    response = undo(client, reviewer, decided["decision"]["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["undone"][0]["undone_by"] == str(reviewer.id)
    assert body["undone"][0]["can_undo"] is False
    [restored] = body["opportunities"]
    assert restored["review_status"] == "pending"
    assert restored["lock_version"] == 2
    assert restored["assigned_to"] is None
    assert restored["decided_at"] is None and restored["decided_by"] is None
    assert body["suppressions_lifted"] == 0
    assert len(audit_rows(db, "opportunity.review_undone")) == 1

    again = undo(client, reviewer, decided["decision"]["id"])
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_undone"


def test_undo_is_refused_after_the_window(
    client: TestClient, db: Session, reviewer: User, pending: Opportunity
) -> None:
    decided = review(client, reviewer, pending, "approve").json()
    age_decision(db, decided["decision"]["id"], minutes=31)

    response = undo(client, reviewer, decided["decision"]["id"])

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "undo_window_closed"
    db.expire_all()
    assert db.get(Opportunity, pending.id).review_status is ReviewStatus.approved  # type: ignore[union-attr]


def test_only_the_decider_or_an_admin_may_undo(
    client: TestClient,
    reviewer: User,
    other_reviewer: User,
    admin_user: User,
    pending: Opportunity,
) -> None:
    decided = review(client, reviewer, pending, "reject", reason_code="ai_mistake").json()

    assert undo(client, other_reviewer, decided["decision"]["id"]).status_code == 403
    allowed = undo(client, admin_user, decided["decision"]["id"])
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["opportunities"][0]["review_status"] == "pending"


def test_undo_of_do_not_contact_lifts_the_suppression_and_restores_every_row(
    client: TestClient, db: Session, reviewer: User, business: Business, pending: Opportunity
) -> None:
    approved = make_opportunity(db, business, "seo_gbp")
    db.commit()
    assert review(client, reviewer, approved, "approve").status_code == 200
    decided = review(client, reviewer, pending, "do_not_contact", note="asked").json()

    response = undo(client, reviewer, decided["decision"]["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["undone"]) == 2
    assert body["suppressions_lifted"] == 1
    statuses = {o["id"]: o["review_status"] for o in body["opportunities"]}
    assert statuses == {str(pending.id): "pending", str(approved.id): "approved"}
    db.expire_all()
    [suppression] = list(db.scalars(select(Suppression)))
    assert suppression.lifted_at is not None and suppression.lifted_by == reviewer.id
    restored = db.get(Opportunity, approved.id)
    assert restored is not None and restored.decided_by == reviewer.id
    assert len(audit_rows(db, "suppression.lifted")) == 1


def test_undo_of_an_unknown_decision_is_404(client: TestClient, reviewer: User) -> None:
    assert undo(client, reviewer, str(uuid.uuid4())).status_code == 404


# --- RBAC matrix ----------------------------------------------------------------------


def test_role_matrix(
    client: TestClient,
    db: Session,
    reviewer: User,
    rep: User,
    crm_manager: User,
    tech_admin: User,
    admin_user: User,
    business: Business,
    pending: Opportunity,
) -> None:
    def status_of(user: User, method: str, path: str, json: Any = None) -> int:
        response = client.request(method, path, json=json, headers=auth_headers(client, user))
        return int(response.status_code)

    queue, detail = "/api/v1/review-queue", f"/api/v1/review-queue/{business.id}"
    decide_body = {"decision": "reject", "lock_version": 0, "reason_code": "ai_mistake"}
    decide = f"/api/v1/opportunities/{pending.id}/review"
    batch_path = "/api/v1/opportunities/review-batch"
    batch_body = {"ids": [str(uuid.uuid4())], "decision": "reject", "reason_code": "ai_mistake"}
    leads = "/api/v1/leads"
    undo_path = f"/api/v1/review-decisions/{uuid.uuid4()}/undo"

    # Sales rep: leads only.
    assert status_of(rep, "GET", queue) == 403
    assert status_of(rep, "GET", detail) == 403
    assert status_of(rep, "POST", decide, decide_body) == 403
    assert status_of(rep, "POST", batch_path, batch_body) == 403
    assert status_of(rep, "POST", undo_path) == 403
    assert status_of(rep, "GET", leads) == 200
    # CRM manager and tech admin: read the queue, decide nothing.
    for reader in (crm_manager, tech_admin):
        assert status_of(reader, "GET", queue) == 200
        assert status_of(reader, "GET", detail) == 200
        assert status_of(reader, "POST", decide, decide_body) == 403
        assert status_of(reader, "POST", batch_path, batch_body) == 403
        assert status_of(reader, "POST", undo_path) == 403
    assert status_of(crm_manager, "GET", leads) == 200
    assert status_of(tech_admin, "GET", leads) == 403
    # Reviewer and admin: everything (the undo of an unknown id is 404, past the role check).
    for decider in (reviewer, admin_user):
        assert status_of(decider, "GET", queue) == 200
        assert status_of(decider, "GET", detail) == 200
        assert status_of(decider, "POST", batch_path, batch_body) == 200
        assert status_of(decider, "POST", undo_path) == 404
        assert status_of(decider, "GET", leads) == 200
    assert status_of(reviewer, "POST", decide, decide_body) == 200
    assert client.get(queue).status_code == 401


# --- queue --------------------------------------------------------------------------------


def queue(client: TestClient, user: User, **params: Any) -> Any:
    response = client.get("/api/v1/review-queue", params=params, headers=auth_headers(client, user))
    assert response.status_code == 200, response.text
    return response.json()


def test_queue_groups_by_business_hides_weak_and_shows_city_and_state(
    client: TestClient, db: Session, reviewer: User
) -> None:
    strong = make_business(db, name="Strong Plumbing")
    make_audit(db, strong)
    make_opportunity(db, strong, "website_design", confidence=0.8, score=0.7)
    make_opportunity(db, strong, "ads_social", confidence=0.3, score=0.9)
    weak_only = make_business(db, name="Weak Signals Only")
    weak_only.city = "Round Rock"
    make_opportunity(db, weak_only, "ads_social", confidence=0.3, score=0.95)
    decided = make_business(db, name="Already Decided")
    make_opportunity(db, decided, "website_design", status=ReviewStatus.approved)
    db.commit()

    page = queue(client, reviewer)
    assert [i["display_name"] for i in page["items"]] == ["Strong Plumbing"]
    [item] = page["items"]
    assert (item["city"], item["state"]) == ("Austin", "TX")
    assert item["industry"] == "plumbing"
    assert item["top_score"] == 0.7, "the weak ads score does not rank the business"
    assert [o["service"] for o in item["opportunities"]] == ["website_design"]
    assert item["opportunities"][0]["lock_version"] == 0
    assert item["opportunities"][0]["service_name"] == "Website design / redesign"
    assert item["weak_hidden"] == 1
    assert item["latest_audit"]["status"] == "done"
    assert item["latest_audit"]["top_findings"] == ["no_online_booking"]

    with_weak = queue(client, reviewer, include_weak="true")
    assert [i["display_name"] for i in with_weak["items"]] == [
        "Weak Signals Only",
        "Strong Plumbing",
    ]
    strong_item = with_weak["items"][1]
    assert strong_item["weak_hidden"] == 0
    assert strong_item["top_score"] == 0.9
    assert [(o["service"], o["weak"]) for o in strong_item["opportunities"]] == [
        ("ads_social", True),
        ("website_design", False),
    ]
    assert with_weak["items"][0]["latest_audit"] is None


def test_queue_filters_status_tab_and_cursor(
    client: TestClient, db: Session, reviewer: User
) -> None:
    first = make_business(db, name="Alpha")
    make_opportunity(db, first, "website_design", score=0.9)
    second = make_business(db, name="Beta Roofing")
    second.industry = "roofing"
    second.city = "Round Rock"
    make_opportunity(db, second, "seo_gbp", score=0.6)
    make_opportunity(db, second, "website_design", score=0.5, status=ReviewStatus.needs_enrichment)
    db.commit()

    def names(**params: Any) -> list[str]:
        return [i["display_name"] for i in queue(client, reviewer, **params)["items"]]

    assert names() == ["Alpha", "Beta Roofing"]
    assert names(service="seo_gbp") == ["Beta Roofing"]
    assert names(city="round rock") == ["Beta Roofing"]
    assert names(state="tx") == ["Alpha", "Beta Roofing"]
    assert names(industry="roofing") == ["Beta Roofing"]
    assert names(min_score=0.7) == ["Alpha"]
    assert names(q="beta") == ["Beta Roofing"]
    assert names(status="needs_enrichment") == ["Beta Roofing"]
    assert (
        client.get(
            "/api/v1/review-queue",
            params={"status": "approved"},
            headers=auth_headers(client, reviewer),
        ).status_code
        == 422
    )

    page = queue(client, reviewer, limit=1)
    assert [i["display_name"] for i in page["items"]] == ["Alpha"]
    assert page["next_cursor"]
    rest = queue(client, reviewer, limit=1, cursor=page["next_cursor"])
    assert [i["display_name"] for i in rest["items"]] == ["Beta Roofing"]
    assert rest["next_cursor"] is None
    bad = client.get(
        "/api/v1/review-queue", params={"cursor": "nope"}, headers=auth_headers(client, reviewer)
    )
    assert bad.status_code == 422


# --- detail -------------------------------------------------------------------------------


def test_detail_has_facts_audit_ai_opportunities_and_history(
    client: TestClient, db: Session, reviewer: User
) -> None:
    from app.modules.opportunities import service as classification
    from tests.integration.test_classification_run import BOOKING_MESSAGE, answer, make_tools

    business = make_business(db, name="Barton Creek Plumbing")
    make_audit(db, business)
    tools = make_tools(answer(("booking_setup", 0.9, BOOKING_MESSAGE, "no_online_booking")))
    classification.classify(db, business, tools=tools)
    db.commit()
    [booking] = list(db.scalars(select(Opportunity).where(Opportunity.business_id == business.id)))
    review(client, reviewer, booking, "needs_enrichment", note="Check hours")

    response = client.get(
        f"/api/v1/review-queue/{business.id}", headers=auth_headers(client, reviewer)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business"]["display_name"] == "Barton Creek Plumbing"
    assert "field_values" in body["business"]
    assert body["audit"]["findings"][0]["code"] == "no_online_booking"
    assert body["audit"]["page_text"] is None, "page text is not part of the review detail"
    assert body["ai"]["ai_generated"] is True
    assert body["ai"]["business_summary"] == "A family-run plumber."
    assert body["ai"]["model"] == "scripted-triage"
    [opportunity] = body["opportunities"]
    assert opportunity["service"] == "booking_setup"
    assert opportunity["source"] == "rules+ai"
    assert opportunity["review_status"] == "needs_enrichment"
    assert opportunity["lock_version"] == 1
    assert opportunity["weak"] is False
    assert [h["decision"] for h in opportunity["history"]] == ["needs_enrichment"]
    assert opportunity["history"][0]["can_undo"] is True
    assert body["suppressed"] is False
    assert body["undo_window_minutes"] == 30
    assert body["weak_confidence"] == 0.4
    missing = client.get(
        f"/api/v1/review-queue/{uuid.uuid4()}", headers=auth_headers(client, reviewer)
    )
    assert missing.status_code == 404


# --- suppression effects --------------------------------------------------------------


def test_detail_tells_a_failed_classification_from_none(
    client: TestClient, db: Session, reviewer: User
) -> None:
    """Spec v0.11.1: a failed call used to leave `ai` null, the same as never classified."""
    from app.modules.ai.client import LLMError
    from app.modules.opportunities import service as classification
    from tests.integration.test_classification_run import make_tools

    never = make_business(db, name="Never Classified")
    make_audit(db, never)
    failed = make_business(db, name="Failed Classification")
    make_audit(db, failed)
    tools = make_tools(
        LLMError("OpenAI did not answer: APIConnectionError: Connection error.", retryable=True)
    )
    classification.classify(db, failed, tools=tools)
    db.commit()

    def detail(business: Business) -> dict[str, Any]:
        response = client.get(
            f"/api/v1/review-queue/{business.id}", headers=auth_headers(client, reviewer)
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    assert detail(never)["ai"] is None
    assert detail(never)["ai_attempt"] is None
    body = detail(failed)
    assert body["ai"] is None
    assert body["ai_attempt"]["status"] == "error"
    assert body["ai_attempt"]["error"] == (
        "OpenAI did not answer: APIConnectionError: Connection error."
    )


def test_after_do_not_contact_the_business_leaves_the_queue_and_the_leads(
    client: TestClient,
    db: Session,
    reviewer: User,
    rep: User,
    business: Business,
    pending: Opportunity,
) -> None:
    lead = make_opportunity(db, business, "seo_gbp", status=ReviewStatus.approved)
    lead.assigned_to = rep.id
    lead.decided_at = NOW
    lead.decided_by = reviewer.id
    db.commit()
    assert [i["business_id"] for i in queue(client, reviewer)["items"]] == [str(business.id)]
    assert len(client.get("/api/v1/leads", headers=auth_headers(client, rep)).json()["items"]) == 1

    review(client, reviewer, pending, "do_not_contact", note="asked")

    assert queue(client, reviewer)["items"] == []
    assert client.get("/api/v1/leads", headers=auth_headers(client, rep)).json()["items"] == []
    detail = client.get(
        f"/api/v1/review-queue/{business.id}", headers=auth_headers(client, reviewer)
    )
    assert detail.json()["suppressed"] is True
    assert detail.json()["suppressions"][0]["source"] == "review"


def test_an_approved_then_suppressed_lead_is_hidden_from_leads(
    client: TestClient,
    db: Session,
    reviewer: User,
    rep: User,
    admin_user: User,
    business: Business,
    pending: Opportunity,
) -> None:
    approved = review(client, reviewer, pending, "approve", assigned_to=str(rep.id))
    assert approved.status_code == 200
    assert len(client.get("/api/v1/leads", headers=auth_headers(client, rep)).json()["items"]) == 1

    added = client.post(
        "/api/v1/suppressions",
        json={"domain": business.domain, "reason": "asked by phone"},
        headers=auth_headers(client, admin_user),
    )
    assert added.status_code == 201, added.text
    assert client.get("/api/v1/leads", headers=auth_headers(client, rep)).json()["items"] == []
    db.expire_all()
    assert db.get(Opportunity, pending.id).review_status is ReviewStatus.approved  # type: ignore[union-attr]


# --- leads --------------------------------------------------------------------------------


def test_leads_are_scoped_to_the_sales_rep_and_filterable(
    client: TestClient,
    db: Session,
    reviewer: User,
    rep: User,
    crm_manager: User,
    business: Business,
) -> None:
    rep2 = make_user(db, Role.sales_rep, "rep2@example.com")
    mine = make_opportunity(db, business, "website_design")
    theirs = make_opportunity(db, business, "seo_gbp")
    nobody = make_opportunity(db, business, "booking_setup")
    db.commit()
    review(client, reviewer, mine, "approve", assigned_to=str(rep.id))
    review(client, reviewer, theirs, "approve", assigned_to=str(rep2.id))
    review(client, reviewer, nobody, "approve")

    for_rep = client.get("/api/v1/leads", headers=auth_headers(client, rep)).json()["items"]
    assert [i["service"] for i in for_rep] == ["website_design"]
    [lead] = for_rep
    assert lead["business_name"] == "Wellington Plumbing"
    assert (lead["city"], lead["state"]) == ("Austin", "TX")
    assert lead["approved_by_email"] == "reviewer@example.com"
    assert lead["assigned_to_email"] == "rep1@example.com"
    assert lead["phone_e164"] == "+15125550100"
    assert lead["website"] == "https://wellington.invalid/"
    assert lead["approved_at"]
    assert lead["score"] == 0.7
    # A rep cannot see someone else's leads by asking for them.
    other = client.get(
        "/api/v1/leads", params={"assigned_to": str(rep2.id)}, headers=auth_headers(client, rep)
    ).json()["items"]
    assert [i["service"] for i in other] == ["website_design"]

    everyone = client.get("/api/v1/leads", headers=auth_headers(client, crm_manager)).json()[
        "items"
    ]
    assert sorted(i["service"] for i in everyone) == ["booking_setup", "seo_gbp", "website_design"]
    filtered = client.get(
        "/api/v1/leads",
        params={"assigned_to": str(rep2.id)},
        headers=auth_headers(client, crm_manager),
    ).json()["items"]
    assert [i["service"] for i in filtered] == ["seo_gbp"]
    by_service = client.get(
        "/api/v1/leads",
        params={"service": "booking_setup", "city": "austin"},
        headers=auth_headers(client, reviewer),
    ).json()["items"]
    assert [i["assigned_to"] for i in by_service] == [None]

    first = client.get(
        "/api/v1/leads", params={"limit": 1}, headers=auth_headers(client, reviewer)
    ).json()
    assert first["next_cursor"]
    second = client.get(
        "/api/v1/leads",
        params={"limit": 1, "cursor": first["next_cursor"]},
        headers=auth_headers(client, reviewer),
    ).json()
    assert second["items"][0]["opportunity_id"] != first["items"][0]["opportunity_id"]


def test_no_contact_data_beyond_the_business_fields_reaches_a_lead(
    client: TestClient, db: Session, reviewer: User, rep: User, pending: Opportunity
) -> None:
    review(client, reviewer, pending, "approve", assigned_to=str(rep.id))
    [lead] = client.get("/api/v1/leads", headers=auth_headers(client, rep)).json()["items"]
    assert set(lead) == {
        "opportunity_id",
        "business_id",
        "business_name",
        "city",
        "state",
        "industry",
        "service",
        "service_name",
        "score",
        "reason",
        "approved_by",
        "approved_by_email",
        "approved_at",
        "assigned_to",
        "assigned_to_email",
        "phone_e164",
        "website",
        "lock_version",
        "top_evidence",
        "rule_reason",
        "ai_rationale",
        # Source codes, not people: which registry rows the business was built from.
        "sources",
        # Data providers Places requires shown (v0.11.1): companies, never people.
        "data_providers",
        "crm",
    }
    # The CRM block is about the record, never about a person.
    assert set(lead["crm"]) == {
        "id",
        "status",
        "external_url",
        "last_synced_at",
        "due_at",
        "last_error",
    }


# --- users picker ---------------------------------------------------------------------


def test_a_reviewer_may_list_active_sales_reps_only(
    client: TestClient, db: Session, reviewer: User, rep: User, crm_manager: User
) -> None:
    inactive = make_user(db, Role.sales_rep)
    update_user(db, inactive.id, UserUpdate(is_active=False))
    db.commit()

    response = client.get(
        "/api/v1/users", params={"role": "sales_rep"}, headers=auth_headers(client, reviewer)
    )
    assert response.status_code == 200, response.text
    assert [u["email"] for u in response.json()["items"]] == ["rep1@example.com"]

    assert client.get("/api/v1/users", headers=auth_headers(client, reviewer)).status_code == 403
    assert (
        client.get(
            "/api/v1/users", params={"role": "admin"}, headers=auth_headers(client, reviewer)
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/users", params={"role": "sales_rep"}, headers=auth_headers(client, rep)
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/users", params={"role": "sales_rep"}, headers=auth_headers(client, crm_manager)
        ).status_code
        == 403
    )


def test_an_admin_lists_users_by_role_and_activity(
    client: TestClient, db: Session, admin_user: User, rep: User, reviewer: User
) -> None:
    inactive = make_user(db, Role.sales_rep)
    update_user(db, inactive.id, UserUpdate(is_active=False))
    db.commit()
    headers = auth_headers(client, admin_user)

    reps = client.get("/api/v1/users", params={"role": "sales_rep"}, headers=headers).json()
    assert sorted(u["email"] for u in reps["items"]) == sorted([rep.email, inactive.email])
    active = client.get(
        "/api/v1/users", params={"role": "sales_rep", "is_active": "true"}, headers=headers
    ).json()
    assert [u["email"] for u in active["items"]] == [rep.email]
    assert db.scalar(select(func.count()).select_from(User)) == 4


# --- lead detail --------------------------------------------------------------------------


def _classification(db: Session, business: Business, *, rationale: str) -> Any:
    from app.modules.ai.models import AIClassification, ClassificationStatus
    from app.modules.audit_web.service import latest_audit

    audit = latest_audit(db, business.id)
    assert audit is not None
    row = AIClassification(
        business_id=business.id,
        website_audit_id=audit.id,
        model="fake-triage",
        prompt_version="classify-1",
        input_hash="abc",
        status=ClassificationStatus.ok,
        output={
            "business_summary": "A plumber.",
            "buying_intent": "none_detected",
            "opportunities": [
                {"service": "website_design", "confidence": 0.9, "rationale": rationale}
            ],
        },
        raw_output="{}",
        rejected_claims=[],
        content_expires_at=None,
    )
    db.add(row)
    db.flush()
    return row


def test_a_sales_rep_can_open_only_a_lead_assigned_to_them(
    client: TestClient,
    db: Session,
    reviewer: User,
    rep: User,
    crm_manager: User,
    tech_admin: User,
    business: Business,
) -> None:
    rep2 = make_user(db, Role.sales_rep, "rep2@example.com")
    mine = make_opportunity(db, business, "website_design")
    theirs = make_opportunity(db, business, "seo_gbp")
    still_pending = make_opportunity(db, business, "booking_setup")
    db.commit()
    assert review(client, reviewer, mine, "approve", assigned_to=str(rep.id)).status_code == 200
    assert review(client, reviewer, theirs, "approve", assigned_to=str(rep2.id)).status_code == 200

    def open_lead(user: User, opportunity: Opportunity | uuid.UUID) -> Any:
        opportunity_id = opportunity.id if isinstance(opportunity, Opportunity) else opportunity
        return client.get(f"/api/v1/leads/{opportunity_id}", headers=auth_headers(client, user))

    ok = open_lead(rep, mine)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["lead"]["opportunity_id"] == str(mine.id)
    assert body["lead"]["assigned_to_email"] == "rep1@example.com"
    assert body["lead"]["approved_by_email"] == "reviewer@example.com"
    assert body["business"]["display_name"] == "Wellington Plumbing"
    assert body["business"]["phone_e164"] == "+15125550100"
    assert body["audit"]["findings"]
    assert body["audit"]["page_text"] is None
    assert body["opportunity"]["review_status"] == "approved"
    assert body["opportunity"]["evidence"][0]["finding_code"] == "no_https"
    assert body["opportunity"]["history"][0]["decision"] == "approve"
    assert body["opportunity"]["rule_reason"] == "Audit found something for website_design."
    assert body["opportunity"]["ai_rationale"] is None

    # Someone else's lead is a 403, not a 404: the rep is told it exists but is not theirs.
    forbidden = open_lead(rep, theirs)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert open_lead(rep2, theirs).status_code == 200

    # A pending opportunity is not a lead for anyone.
    assert open_lead(rep, still_pending).status_code == 404
    assert open_lead(reviewer, still_pending).status_code == 404
    assert open_lead(reviewer, uuid.uuid4()).status_code == 404

    # Every role that lists leads may open one; a tech admin may not.
    assert open_lead(reviewer, theirs).status_code == 200
    assert open_lead(crm_manager, theirs).status_code == 200
    assert open_lead(tech_admin, theirs).status_code == 403


def test_a_suppressed_lead_cannot_be_opened(
    client: TestClient, db: Session, reviewer: User, rep: User, business: Business
) -> None:
    mine = make_opportunity(db, business, "website_design")
    other = make_opportunity(db, business, "seo_gbp")
    db.commit()
    review(client, reviewer, mine, "approve", assigned_to=str(rep.id))
    assert (
        client.get(f"/api/v1/leads/{mine.id}", headers=auth_headers(client, rep)).status_code == 200
    )
    review(client, reviewer, other, "do_not_contact", note="Owner asked us to stop")
    assert (
        client.get(f"/api/v1/leads/{mine.id}", headers=auth_headers(client, rep)).status_code == 404
    )


def test_the_reason_is_split_into_the_rule_wording_and_the_ai_rationale(
    client: TestClient, db: Session, reviewer: User, rep: User, business: Business
) -> None:
    rationale = "The model also read a 2016 copyright line."
    classification = _classification(db, business, rationale=rationale)
    both = make_opportunity(db, business, "website_design", source=OpportunitySource.rules_and_ai)
    both.reason = f"Audit found the homepage served over http, not https. {rationale}"
    both.ai_classification_id = classification.id
    ai_only = make_opportunity(db, business, "seo_gbp", source=OpportunitySource.ai)
    ai_only.reason = "The model saw no meta description."
    ai_only.ai_classification_id = classification.id
    rules_only = make_opportunity(db, business, "booking_setup")
    db.commit()

    detail = client.get(
        f"/api/v1/review-queue/{business.id}", headers=auth_headers(client, reviewer)
    ).json()
    by_service = {item["service"]: item for item in detail["opportunities"]}
    assert by_service["website_design"]["rule_reason"] == (
        "Audit found the homepage served over http, not https."
    )
    assert by_service["website_design"]["ai_rationale"] == rationale
    assert by_service["seo_gbp"]["rule_reason"] is None
    assert by_service["seo_gbp"]["ai_rationale"] == "The model saw no meta description."
    assert by_service["booking_setup"]["rule_reason"] == rules_only.reason
    assert by_service["booking_setup"]["ai_rationale"] is None

    review(client, reviewer, both, "approve", assigned_to=str(rep.id))
    [lead] = client.get("/api/v1/leads", headers=auth_headers(client, rep)).json()["items"]
    assert lead["rule_reason"] == "Audit found the homepage served over http, not https."
    assert lead["ai_rationale"] == rationale
