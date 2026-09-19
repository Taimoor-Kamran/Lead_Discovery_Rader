"""Auditing one business, and reading audits back.

The shape of one audit:

1. decide what to fetch. A business with no website, or only a social profile, is audited
   with **zero network calls** — the finding is about the listing, not about a page;
2. ask `robots.txt` first. Disallowed means the homepage is never requested at all;
3. fetch the homepage through the SSRF guard, once;
4. run the deterministic checks, then ask PageSpeed for the mobile numbers;
5. turn the gaps into findings, each carrying the text and URL it came from.

Every step that can fail produces a *status* rather than an exception, because an audit
that did not work is still information about that business. The only thing that raises is
a bug in our own code, and the worker catches that per business too.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.pagination import DEFAULT_LIMIT, Page, apply_cursor, encode_cursor
from app.core.safe_fetch import FetchOutcome, SafeFetcher, UnsafeUrlError, build_fetcher
from app.modules.audit_web import checks as checks_module
from app.modules.audit_web import findings as findings_module
from app.modules.audit_web.models import RULES_VERSION, AuditStatus, WebsiteAudit
from app.modules.audit_web.psi import (
    PageSpeedClient,
    PageSpeedUnavailableError,
    build_psi_client,
)
from app.modules.audit_web.schemas import WebsiteAuditDetail, WebsiteAuditSummary
from app.modules.businesses.models import Business
from app.modules.jobs.models import JobRun as JobRunType
from app.modules.normalization.schemas import BusinessStatus, WebsiteKind

logger = get_logger("app.audit_web")

# The two kinds of website worth fetching. A social profile is recorded, never fetched —
# the blueprint's hard rule — and a business with no website has nothing to fetch.
AUDITABLE_WEBSITE_KINDS = frozenset({WebsiteKind.own_site, WebsiteKind.builder_subdomain})


@dataclass
class AuditTools:
    """The two outside things an audit needs. Injected so a test can drive either one."""

    fetcher: SafeFetcher
    psi: PageSpeedClient
    settings: Settings = field(default_factory=get_settings)

    @classmethod
    def build(cls, *, job_run_id: uuid.UUID | None = None) -> "AuditTools":
        settings = get_settings()
        return cls(
            fetcher=build_fetcher(settings=settings),
            psi=build_psi_client(job_run_id=job_run_id, settings=settings),
            settings=settings,
        )


# --- auditing one business --------------------------------------------------------------


def audit_business(
    session: Session,
    business: Business,
    *,
    tools: AuditTools,
    job_run_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> WebsiteAudit:
    """Audit one business's homepage and store the result. Never raises for a bad site."""
    settings = tools.settings
    started = now or datetime.now(UTC)
    context = findings_module.FindingContext(
        industry=business.industry,
        website_kind=business.website_kind,
        website=business.website,
        booking_industries=frozenset(item.lower() for item in settings.audit_booking_industries),
        slow_mobile_score=settings.audit_slow_mobile_score,
        stale_copyright_years=settings.audit_stale_copyright_years,
        now=started,
    )

    if business.website_kind not in AUDITABLE_WEBSITE_KINDS or not business.website:
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=business.website or "",
            status=AuditStatus.skipped,
            findings=findings_module.for_missing_website(
                context, business_label=business.display_name
            ),
            started_at=started,
            settings=settings,
        )

    url = business.website
    try:
        robots = tools.fetcher.robots(url)
    except UnsafeUrlError as exc:
        return _refused(session, business, job_run_id, url, exc, started, settings)

    # A certificate that does not verify, or a host that never answers, shows up on the
    # robots request — before the homepage is asked for. Neither is a robots decision, so
    # each is recorded as what it is (`tls_invalid`, `unreachable`) and the homepage is
    # still never requested. A timeout or a 5xx *is* left to the robots policy: the server
    # is there, it just would not tell us the rules.
    if not robots.tls_valid or robots.error_kind == "connect":
        checks = checks_module.fetch_checks(
            _failed_outcome(url, robots.reason, robots.error_kind, tls_valid=robots.tls_valid)
        )
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=url,
            status=AuditStatus.unreachable,
            checks=checks,
            findings=findings_module.for_unreachable(checks, url),
            started_at=started,
            settings=settings,
        )

    if not robots.allowed:
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=url,
            status=AuditStatus.robots_blocked,
            findings=findings_module.for_robots_blocked(robots.reason, robots.robots_url),
            started_at=started,
            settings=settings,
        )

    try:
        outcome = tools.fetcher.fetch(url)
    except UnsafeUrlError as exc:
        return _refused(session, business, job_run_id, url, exc, started, settings)

    checks = checks_module.fetch_checks(outcome)
    final_url = outcome.final_url or url

    if not outcome.reachable or (outcome.status_code or 0) >= 500:
        return _store(
            session,
            business=business,
            job_run_id=job_run_id,
            url_audited=url,
            final_url=final_url,
            status=AuditStatus.unreachable,
            http_status=outcome.status_code,
            checks=checks,
            findings=findings_module.for_unreachable(checks, final_url),
            html_sha256=outcome.html_sha256,
            started_at=started,
            settings=settings,
        )

    html, page_text = checks_module.analyse_html(
        outcome, now=started, page_text_limit=settings.audit_page_text_max_chars
    )
    checks.update(html)
    psi, psi_error = _measure(tools.psi, final_url)
    if psi_error is not None:
        checks["psi_error"] = checks_module.CheckResult(
            psi_error, evidence_text=psi_error, evidence_url=final_url
        )

    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        final_url=final_url,
        status=AuditStatus.done,
        http_status=outcome.status_code,
        checks=checks,
        psi=psi,
        findings=findings_module.for_page(checks, psi, context),
        page_text=page_text,
        html_sha256=outcome.html_sha256,
        started_at=started,
        settings=settings,
    )


def record_failure(
    session: Session,
    business: Business,
    *,
    reason: str,
    job_run_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> WebsiteAudit:
    """Store a `failed` audit for a business whose audit hit a bug in our own code.

    Deliberately separate from the statuses above: `unreachable` is a fact about the
    website, `failed` is a fact about us, and a salesperson must be able to tell them
    apart.
    """
    settings = get_settings()
    audit = _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=business.website or "",
        status=AuditStatus.failed,
        checks={
            "error": checks_module.CheckResult(
                reason, evidence_text=reason, evidence_url=business.website
            )
        },
        findings=[],
        started_at=now or datetime.now(UTC),
        settings=settings,
    )
    logger.warning(
        "a business audit failed",
        extra={"business_id": str(business.id), "reason": reason},
    )
    return audit


def _measure(psi: PageSpeedClient, url: str) -> tuple[dict[str, Any] | None, str | None]:
    """PageSpeed, or the reason there is none. Never fails the audit around it."""
    try:
        return psi.analyse(url).as_dict(), None
    except PageSpeedUnavailableError as exc:
        logger.info("pagespeed was unavailable", extra={"url": url, "reason": exc.reason})
        return None, exc.reason


def _failed_outcome(
    url: str, reason: str, kind: str | None, *, tls_valid: bool = True
) -> FetchOutcome:
    """A fetch that never happened, in the shape the checks read."""
    return FetchOutcome(url=url, final_url=url, tls_valid=tls_valid, error=reason, error_kind=kind)


def _refused(
    session: Session,
    business: Business,
    job_run_id: uuid.UUID | None,
    url: str,
    exc: UnsafeUrlError,
    started: datetime,
    settings: Settings,
) -> WebsiteAudit:
    """A URL the SSRF guard would not request. Recorded as unreachable, with the reason."""
    checks = checks_module.fetch_checks(_failed_outcome(url, exc.reason, "refused"))
    logger.info(
        "an audit target was refused by the fetch guard",
        extra={"business_id": str(business.id), "reason": exc.reason},
    )
    return _store(
        session,
        business=business,
        job_run_id=job_run_id,
        url_audited=url,
        status=AuditStatus.unreachable,
        checks=checks,
        findings=findings_module.for_unreachable(checks, url),
        started_at=started,
        settings=settings,
    )


def _store(
    session: Session,
    *,
    business: Business,
    job_run_id: uuid.UUID | None,
    url_audited: str,
    status: AuditStatus,
    findings: list[findings_module.Finding],
    started_at: datetime,
    settings: Settings,
    final_url: str | None = None,
    http_status: int | None = None,
    checks: checks_module.Checks | None = None,
    psi: dict[str, Any] | None = None,
    page_text: str | None = None,
    html_sha256: str | None = None,
) -> WebsiteAudit:
    resolved_checks = checks or {}
    tech_stack = checks_module.value_of(resolved_checks, "tech_stack") or {}
    ttl_days = settings.audit_content_ttl_days
    audit = WebsiteAudit(
        business_id=business.id,
        job_run_id=job_run_id,
        url_audited=url_audited,
        final_url=final_url,
        status=status,
        http_status=http_status,
        checks=checks_module.as_payload(resolved_checks),
        psi=psi,
        tech_stack=tech_stack,
        findings=findings_module.as_payload(findings),
        page_text=page_text,
        html_sha256=html_sha256,
        rules_version=RULES_VERSION,
        content_expires_at=(
            started_at + timedelta(days=ttl_days) if ttl_days > 0 and page_text else None
        ),
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )
    session.add(audit)
    session.flush()
    logger.info(
        "website audited",
        extra={
            "business_id": str(business.id),
            "website_audit_id": str(audit.id),
            "status": status.value,
            "findings": audit.finding_codes,
        },
    )
    return audit


# --- enqueueing ---------------------------------------------------------------------------

# Imported by name rather than from `jobs.service`, which imports this module back.
RESOLUTION_JOB_KIND = "resolution"


def enqueue_audits_for_run(
    session: Session,
    resolution_run_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> JobRunType:
    """Queue an audit run for the businesses one resolution run touched.

    Only a resolution run can be audited: it is what says which businesses just changed.
    Asking for anything else is a 422 rather than a run that would find nothing.
    """
    from app.modules.jobs.service import AUDIT_JOB_KIND, enqueue_run, get_job_run

    parent = get_job_run(session, resolution_run_id)
    if parent.kind != RESOLUTION_JOB_KIND:
        raise ValidationFailedError(
            "Only a resolution run's businesses can be audited",
            details={"job_run_id": str(resolution_run_id), "kind": parent.kind},
        )
    return enqueue_run(
        session,
        search_job_id=parent.search_job_id,
        kind=AUDIT_JOB_KIND,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        params={"parent_run_id": str(parent.id)},
    )


def enqueue_audit_for_business(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> JobRunType:
    """Queue a fresh audit of one business now, however recently it was last audited."""
    from app.modules.businesses.service import get_business
    from app.modules.jobs.service import AUDIT_JOB_KIND, enqueue_run

    business = get_business(session, business_id)
    return enqueue_run(
        session,
        search_job_id=None,
        kind=AUDIT_JOB_KIND,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        params={"business_id": str(business.id)},
    )


# --- choosing what to audit -------------------------------------------------------------


def businesses_for_run(
    session: Session, resolution_run_id: uuid.UUID, *, now: datetime | None = None
) -> list[Business]:
    """The businesses one resolution run touched, minus those audited recently enough.

    "Touched" means linked or created while resolving the discovery run this resolution
    run was pointed at, which is exactly the set of businesses whose facts just changed.
    """
    from app.modules.discovery.models import DiscoveredRecord, RecordSighting

    resolution_run = session.get(JobRunType, resolution_run_id)
    if resolution_run is None:
        raise NotFoundError("Job run not found", details={"job_run_id": str(resolution_run_id)})
    discovery_run_id = (resolution_run.params or {}).get("parent_run_id")
    if not discovery_run_id:
        raise ValidationFailedError(
            "That resolution run does not say which discovery run it resolved",
            details={"job_run_id": str(resolution_run_id)},
        )

    business_ids = list(
        session.scalars(
            select(DiscoveredRecord.business_id)
            .join(RecordSighting, RecordSighting.discovered_record_id == DiscoveredRecord.id)
            .where(
                RecordSighting.job_run_id == uuid.UUID(str(discovery_run_id)),
                DiscoveredRecord.business_id.is_not(None),
            )
            .distinct()
        )
    )
    if not business_ids:
        return []

    businesses = list(
        session.scalars(
            select(Business)
            .where(
                Business.id.in_(business_ids),
                # A business that has closed for good is not a lead, so it is not audited.
                Business.business_status != BusinessStatus.closed_permanently,
            )
            .order_by(Business.created_at.asc(), Business.id.asc())
        )
    )
    return [b for b in businesses if needs_audit(session, b, now=now)]


def needs_audit(session: Session, business: Business, *, now: datetime | None = None) -> bool:
    """Whether the newest audit for this business is missing or too old to trust."""
    latest = latest_audit(session, business.id)
    if latest is None:
        return True
    max_age = get_settings().audit_max_age_days
    if max_age <= 0:
        return True
    return latest.created_at < (now or datetime.now(UTC)) - timedelta(days=max_age)


# --- reads --------------------------------------------------------------------------------


def get_audit(session: Session, audit_id: uuid.UUID) -> WebsiteAudit:
    audit = session.get(WebsiteAudit, audit_id)
    if audit is None:
        raise NotFoundError("Website audit not found", details={"website_audit_id": str(audit_id)})
    return audit


def latest_audit(session: Session, business_id: uuid.UUID) -> WebsiteAudit | None:
    return session.scalars(
        select(WebsiteAudit)
        .where(WebsiteAudit.business_id == business_id)
        .order_by(WebsiteAudit.created_at.desc(), WebsiteAudit.id.desc())
        .limit(1)
    ).first()


def latest_audits(session: Session, business_ids: list[uuid.UUID]) -> dict[uuid.UUID, WebsiteAudit]:
    """The newest audit per business, in one query. Used by the business list view."""
    if not business_ids:
        return {}
    ranked = _latest_audit_subquery()
    rows = session.scalars(
        select(WebsiteAudit)
        .join(ranked, ranked.c.id == WebsiteAudit.id)
        .where(WebsiteAudit.business_id.in_(business_ids))
    )
    return {row.business_id: row for row in rows}


def _latest_audit_subquery() -> Any:
    """`(id, business_id, status, findings)` of the newest audit of each business."""
    ranked = select(
        WebsiteAudit.id,
        WebsiteAudit.business_id,
        WebsiteAudit.status,
        WebsiteAudit.findings,
        func.row_number()
        .over(
            partition_by=WebsiteAudit.business_id,
            order_by=(WebsiteAudit.created_at.desc(), WebsiteAudit.id.desc()),
        )
        .label("rank"),
    ).subquery()
    return select(ranked).where(ranked.c.rank == 1).subquery()


def apply_audit_filters(
    stmt: Select[tuple[Business]],
    *,
    finding_codes: list[str] | None,
    audit_status: AuditStatus | None,
) -> Select[tuple[Business]]:
    """Narrow a business query by what its **newest** audit found.

    The filters combine with AND: `finding=no_https&finding=no_online_booking` returns
    only businesses whose latest audit found both.
    """
    if not finding_codes and audit_status is None:
        return stmt
    latest = _latest_audit_subquery()
    stmt = stmt.join(latest, latest.c.business_id == Business.id)
    if audit_status is not None:
        stmt = stmt.where(latest.c.status == audit_status)
    for code in finding_codes or []:
        # A GIN containment query, which is what `ix_website_audits_findings` is for.
        stmt = stmt.where(latest.c.findings.contains([{"code": code}]))
    return stmt


def list_audits_for_business(
    session: Session,
    business_id: uuid.UUID,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page[WebsiteAuditSummary]:
    """One business's audit history, newest first."""
    stmt = (
        select(WebsiteAudit)
        .where(WebsiteAudit.business_id == business_id)
        .order_by(WebsiteAudit.created_at.desc(), WebsiteAudit.id.desc())
        .limit(limit + 1)
    )
    stmt = apply_cursor(stmt, WebsiteAudit.created_at, WebsiteAudit.id, cursor)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return Page[WebsiteAuditSummary](
        items=[summarize(row) for row in rows], next_cursor=next_cursor
    )


def summarize(audit: WebsiteAudit) -> WebsiteAuditSummary:
    return WebsiteAuditSummary(
        id=audit.id,
        business_id=audit.business_id,
        job_run_id=audit.job_run_id,
        url_audited=audit.url_audited,
        final_url=audit.final_url,
        status=audit.status,
        http_status=audit.http_status,
        finding_codes=audit.finding_codes,
        rules_version=audit.rules_version,
        started_at=audit.started_at,
        finished_at=audit.finished_at,
        created_at=audit.created_at,
    )


def detail(audit: WebsiteAudit, *, include_page_text: bool) -> WebsiteAuditDetail:
    """The whole audit. `page_text` is only for the roles allowed to read it."""
    return WebsiteAuditDetail(
        **summarize(audit).model_dump(),
        checks=audit.checks or {},
        psi=audit.psi,
        tech_stack=audit.tech_stack or {},
        findings=audit.findings or [],
        page_text=audit.page_text if include_page_text else None,
        page_text_hidden=bool(audit.page_text) and not include_page_text,
        html_sha256=audit.html_sha256,
        content_expires_at=audit.content_expires_at,
        purged_at=audit.purged_at,
    )


# --- retention ----------------------------------------------------------------------------


def purge_expired_page_text(session: Session, *, now: datetime | None = None) -> int:
    """Null the stored page text of audits past their retention window.

    Only `page_text` goes. The checks, the findings and their evidence snippets are our
    own observations with their own provenance, and they are what a salesperson works
    from — so they stay, and the audit row stays alongside them.
    """
    moment = now or datetime.now(UTC)
    result = session.execute(
        update(WebsiteAudit)
        .where(
            WebsiteAudit.content_expires_at.is_not(None),
            WebsiteAudit.content_expires_at < moment,
            WebsiteAudit.page_text.is_not(None),
        )
        .values(page_text=None, purged_at=moment)
    )
    purged = int(getattr(result, "rowcount", 0) or 0)
    if purged:
        logger.info("purged expired audit page text", extra={"audits": purged})
    return purged
