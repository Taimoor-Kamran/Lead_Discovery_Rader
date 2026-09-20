"""`/admin/health` and `/admin/alerts`: the in-app monitoring for admins and tech admins."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.alerts import service as alerts
from app.modules.alerts.schemas import AlertRead
from app.modules.auth.deps import DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.monitoring import service
from app.modules.monitoring.schemas import HealthReport

admin_router = APIRouter(prefix="/admin", tags=["admin"])

Operator = Annotated[User, Depends(require_role(Role.tech_admin))]


@admin_router.get("/health", response_model=HealthReport)
def admin_health(actor: Operator, session: DbSession) -> HealthReport:
    """Every metric from blueprint slide 45, with the alert rules re-evaluated."""
    return service.health(session)


@admin_router.get("/alerts", response_model=list[AlertRead])
def list_alerts(
    actor: Operator,
    session: DbSession,
    include_acknowledged: Annotated[bool, Query()] = False,
    active_only: Annotated[bool, Query()] = True,
) -> list[AlertRead]:
    return alerts.list_alerts(
        session, active_only=active_only, include_acknowledged=include_acknowledged
    )


@admin_router.post("/alerts/{alert_id}/acknowledge", response_model=AlertRead)
def acknowledge_alert(alert_id: uuid.UUID, actor: Operator, session: DbSession) -> AlertRead:
    """Hide the alert from the banner. Audited with the acknowledging user."""
    return alerts.read(alerts.acknowledge(session, alert_id, actor_id=actor.id))
