"""The audit log is append-only and records every state change."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from tests.conftest import auth_headers, geo_payload


def test_an_audit_row_cannot_be_updated(db: Session, sales_user: User) -> None:
    entry = audit.record(db, action="test.event", entity_type="user", entity_id=sales_user.id)
    db.commit()

    with pytest.raises(DatabaseError):
        db.execute(
            text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"), {"id": entry.id}
        )
    db.rollback()

    assert db.scalar(select(AuditLog.action).where(AuditLog.id == entry.id)) == "test.event"


def test_an_audit_row_cannot_be_deleted(db: Session, sales_user: User) -> None:
    entry = audit.record(db, action="test.event", entity_type="user", entity_id=sales_user.id)
    db.commit()

    with pytest.raises(DatabaseError):
        db.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": entry.id})
    db.rollback()

    assert db.scalar(select(AuditLog.id).where(AuditLog.id == entry.id)) == entry.id


def test_an_audit_row_records_who_did_what(db: Session, sales_user: User) -> None:
    entry = audit.record(
        db,
        action="search_job.created",
        entity_type="search_job",
        entity_id="abc",
        actor_id=sales_user.id,
        before=None,
        after={"status": "draft"},
    )
    db.commit()
    assert entry.actor_id == sales_user.id
    assert entry.after == {"status": "draft"}
    assert entry.created_at is not None


def test_every_job_status_change_is_audited(
    client: TestClient, db: Session, sales_user: User
) -> None:
    from app.modules.jobs.models import JobRunStatus
    from app.workers import tasks

    headers = auth_headers(client, sales_user)
    job_id = client.post(
        "/api/v1/search-jobs",
        json={"name": "audited", "geo": geo_payload(), "industry": "dental", "source_ids": []},
        headers=headers,
    ).json()["id"]
    run_id = client.post(f"/api/v1/search-jobs/{job_id}/run", headers=headers).json()["id"]
    tasks.execute_job_run(run_id)

    db.expire_all()
    rows = list(
        db.scalars(select(AuditLog).where(AuditLog.entity_id == run_id).order_by(AuditLog.id))
    )
    actions = [row.action for row in rows]
    assert actions == ["job_run.enqueued", "job_run.status_changed", "job_run.status_changed"]

    transitions = [(row.before, row.after) for row in rows if row.before and row.after]
    assert transitions[0][0] == {"status": JobRunStatus.queued.value}
    assert transitions[-1][1]["status"] == JobRunStatus.done.value
