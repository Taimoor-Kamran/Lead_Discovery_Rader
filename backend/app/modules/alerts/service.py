"""Raising, clearing and acknowledging alerts (blueprint slide 45).

Two kinds of rule share the table:

* **condition** rules (`job_success_rate`, `source_error_rate`, `crm_held`, `ai_budget`,
  `backup_age`, `queue_length`) are re-evaluated by the health report; `sync_condition`
  raises when the condition holds and clears the row once it no longer does;
* **event** rules (`stale_job`, `backup_verify_failed`) are raised by the thing that
  noticed them and are cleared when a human acknowledges them.

Every new alert is one `WARNING` log line, so the rotating log files carry the same story
as the banner.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.modules.alerts.models import Alert
from app.modules.alerts.schemas import AlertRead
from app.modules.audit import service as audit

logger = get_logger("app.alerts")

RULE_JOB_SUCCESS_RATE = "job_success_rate"
RULE_SOURCE_ERROR_RATE = "source_error_rate"
RULE_CRM_HELD = "crm_held"
RULE_AI_BUDGET = "ai_budget"
RULE_BACKUP_AGE = "backup_age"
RULE_BACKUP_VERIFY_FAILED = "backup_verify_failed"
RULE_STALE_JOB = "stale_job"
RULE_QUEUE_LENGTH = "queue_length"

EVENT_RULES = frozenset({RULE_BACKUP_VERIFY_FAILED, RULE_STALE_JOB})
ALL_RULES = (
    RULE_JOB_SUCCESS_RATE,
    RULE_SOURCE_ERROR_RATE,
    RULE_CRM_HELD,
    RULE_AI_BUDGET,
    RULE_BACKUP_AGE,
    RULE_BACKUP_VERIFY_FAILED,
    RULE_STALE_JOB,
    RULE_QUEUE_LENGTH,
)


def active_alert(session: Session, rule: str) -> Alert | None:
    return session.scalars(
        select(Alert)
        .where(Alert.rule == rule, Alert.cleared_at.is_(None))
        .order_by(Alert.first_seen_at.desc())
        .limit(1)
    ).first()


def raise_alert(
    session: Session,
    rule: str,
    message: str,
    *,
    severity: str = "warning",
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Alert:
    """Open the alert for `rule`, or refresh the one already open. Logs a WARNING when new."""
    moment = now or datetime.now(UTC)
    existing = active_alert(session, rule)
    if existing is not None:
        existing.last_seen_at = moment
        existing.message = message
        existing.details = dict(details or {})
        session.flush()
        return existing

    alert = Alert(
        rule=rule,
        severity=severity,
        message=message,
        details=dict(details or {}),
        first_seen_at=moment,
        last_seen_at=moment,
    )
    session.add(alert)
    session.flush()
    audit.record(
        session,
        action="alert.raised",
        entity_type="alert",
        entity_id=alert.id,
        after={"rule": rule, "severity": severity, "message": message, "details": alert.details},
    )
    logger.warning(
        "alert raised", extra={"rule": rule, "severity": severity, "alert_message": message}
    )
    return alert


def clear_alert(session: Session, rule: str, *, now: datetime | None = None) -> bool:
    """Close the open alert for `rule`, if any. Returns whether one was closed."""
    moment = now or datetime.now(UTC)
    existing = active_alert(session, rule)
    if existing is None:
        return False
    existing.cleared_at = moment
    session.flush()
    audit.record(
        session,
        action="alert.cleared",
        entity_type="alert",
        entity_id=existing.id,
        after={"rule": rule},
    )
    logger.info("alert cleared", extra={"rule": rule})
    return True


def sync_condition(
    session: Session,
    rule: str,
    *,
    holds: bool,
    message: str,
    details: dict[str, Any] | None = None,
    severity: str = "warning",
    now: datetime | None = None,
) -> Alert | None:
    """Keep a condition rule's alert in step with whether the condition holds right now."""
    if holds:
        return raise_alert(session, rule, message, severity=severity, details=details, now=now)
    clear_alert(session, rule, now=now)
    return None


def get_alert(session: Session, alert_id: uuid.UUID) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError("Alert not found", details={"alert_id": str(alert_id)})
    return alert


def acknowledge(
    session: Session, alert_id: uuid.UUID, *, actor_id: uuid.UUID, now: datetime | None = None
) -> Alert:
    """Hide an alert from the banner. An event alert is also closed; a condition alert
    stays open (hidden) until its condition clears, so it cannot re-fire while it persists.
    """
    moment = now or datetime.now(UTC)
    alert = get_alert(session, alert_id)
    if alert.acknowledged_at is None:
        alert.acknowledged_at = moment
        alert.acknowledged_by = actor_id
        if alert.rule in EVENT_RULES and alert.cleared_at is None:
            alert.cleared_at = moment
        session.flush()
        audit.record(
            session,
            action="alert.acknowledged",
            entity_type="alert",
            entity_id=alert.id,
            actor_id=actor_id,
            after={"rule": alert.rule, "cleared": alert.cleared_at is not None},
        )
    return alert


def list_alerts(
    session: Session, *, active_only: bool = True, include_acknowledged: bool = False
) -> list[AlertRead]:
    stmt = select(Alert).order_by(Alert.first_seen_at.desc(), Alert.id.desc()).limit(200)
    if active_only:
        stmt = stmt.where(Alert.cleared_at.is_(None))
    if not include_acknowledged:
        stmt = stmt.where(Alert.acknowledged_at.is_(None))
    return [read(alert) for alert in session.scalars(stmt)]


def read(alert: Alert) -> AlertRead:
    return AlertRead(
        id=alert.id,
        rule=alert.rule,
        severity=alert.severity,
        message=alert.message,
        details=dict(alert.details),
        first_seen_at=alert.first_seen_at,
        last_seen_at=alert.last_seen_at,
        acknowledged_at=alert.acknowledged_at,
        acknowledged_by=alert.acknowledged_by,
        cleared_at=alert.cleared_at,
        active=alert.active,
        acknowledged=alert.acknowledged,
    )
