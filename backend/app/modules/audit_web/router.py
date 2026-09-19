"""`/businesses/{id}/audit(s)`, `/jobs/{id}/audit` and `/website-audits/{id}`."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.modules.audit_web import service
from app.modules.audit_web.schemas import WebsiteAuditDetail, WebsiteAuditSummary
from app.modules.auth.deps import CurrentUser, DbSession, require_role
from app.modules.auth.models import Role, User
from app.modules.jobs.schemas import JobRunRead

business_audits_router = APIRouter(prefix="/businesses", tags=["website-audits"])
job_audits_router = APIRouter(prefix="/jobs", tags=["website-audits"])
website_audits_router = APIRouter(prefix="/website-audits", tags=["website-audits"])

# Anyone who works a lead may ask for a fresh audit of it: the audit is read-only towards
# the outside world and costs one page fetch. Only `crm_manager` has no reason to.
AuditRequester = Annotated[
    User, Depends(require_role(Role.tech_admin, Role.reviewer, Role.sales_rep))
]
RunOperator = Annotated[User, Depends(require_role(Role.tech_admin))]

# `page_text` is the raw text of somebody else's website, kept as input for the v0.5.0 AI
# step. The roles that need to see it are the ones who check the machine's work.
PAGE_TEXT_ROLES = frozenset({Role.admin, Role.reviewer, Role.tech_admin})


@business_audits_router.post(
    "/{business_id}/audit", response_model=JobRunRead, status_code=status.HTTP_202_ACCEPTED
)
def audit_business_now(
    business_id: uuid.UUID,
    actor: AuditRequester,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobRunRead:
    """Queue a fresh audit of one business, however recently it was last audited."""
    run = service.enqueue_audit_for_business(
        session, business_id, actor_id=actor.id, idempotency_key=idempotency_key
    )
    return JobRunRead.model_validate(run)


@business_audits_router.get("/{business_id}/audits", response_model=Page[WebsiteAuditSummary])
def list_business_audits(
    business_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[WebsiteAuditSummary]:
    """This business's audit history, newest first."""
    from app.modules.businesses.service import get_business

    get_business(session, business_id)
    return service.list_audits_for_business(session, business_id, limit=limit, cursor=cursor)


@job_audits_router.post(
    "/{job_run_id}/audit", response_model=JobRunRead, status_code=status.HTTP_202_ACCEPTED
)
def audit_job_run(
    job_run_id: uuid.UUID,
    actor: RunOperator,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobRunRead:
    """Audit the businesses a resolution run touched. Skips anything audited recently."""
    run = service.enqueue_audits_for_run(
        session, job_run_id, actor_id=actor.id, idempotency_key=idempotency_key
    )
    return JobRunRead.model_validate(run)


@website_audits_router.get("/{website_audit_id}", response_model=WebsiteAuditDetail)
def get_website_audit(
    website_audit_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> WebsiteAuditDetail:
    """One audit in full: every check, every finding and the evidence behind both."""
    audit = service.get_audit(session, website_audit_id)
    return service.detail(audit, include_page_text=user.role in PAGE_TEXT_ROLES)
