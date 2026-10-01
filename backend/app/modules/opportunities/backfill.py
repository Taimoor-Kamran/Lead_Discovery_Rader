"""v0.12.1 one-off: withdraw the pending opportunities orphaned before withdrawal existed.

Run by a human through `scripts/backfill_withdrawn.py`, dry run by default. There is no
merged result to compare with here, so only the cited-findings test is applied: a pending
row that cites at least one finding, none of which is in its business's latest audit. A
row that cites nothing (`ads_social`) is never listed (correction C).

Only a latest audit that looked can show a finding is gone: `done` or `skipped` for page
evidence, a working DNS / RDAP lookup for domain evidence (v0.13.0, amendment W). A row whose
latest audit could not look is listed as skipped and never written (correction H).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.audit_web.models import WebsiteAudit
from app.modules.audit_web.service import latest_audits
from app.modules.businesses.models import Business
from app.modules.normalization.schemas import BusinessStatus
from app.modules.opportunities.models import Opportunity, ReviewStatus
from app.modules.opportunities.service import (
    OPEN_STATUSES,
    can_judge,
    cited_findings,
    withdrawal_reasons,
)

logger = get_logger("app.opportunities.backfill")


@dataclass(frozen=True)
class Orphan:
    opportunity_id: str
    business_name: str
    service: str
    review_status: str
    cited: tuple[str, ...]
    reason: str
    withdrawn: bool
    # The latest audit's status when it could not look (correction H): never written.
    skipped_audit_status: str | None = None


def find_orphans(
    session: Session, *, apply: bool = False, now: datetime | None = None
) -> list[Orphan]:
    """Every open or approved row whose cited findings are all gone; withdraw only with `apply`.

    Only a live `pending` row is ever written. `approved` and `needs_enrichment` rows in
    the same situation are listed with `withdrawn=False` and logged at warning level.
    """
    moment = now or datetime.now(UTC)
    rows = list(
        session.execute(
            select(Opportunity, Business)
            .join(Business, Business.id == Opportunity.business_id)
            .where(
                Opportunity.review_status.in_((*OPEN_STATUSES, ReviewStatus.approved)),
                Opportunity.withdrawn_at.is_(None),
            )
            .order_by(Business.display_name, Opportunity.service, Opportunity.id)
        ).tuples()
    )
    audits = latest_audits(session, list({business.id for _, business in rows}))
    found: list[Orphan] = []
    for row, business in rows:
        audit = audits.get(business.id)
        if audit is None:
            continue
        reasons = withdrawal_reasons(row, audit=audit, merged_services=None, merged_complete=False)
        if not reasons:
            continue
        reason = "; ".join(reasons)
        if not can_judge(row, audit):
            found.append(
                Orphan(
                    opportunity_id=str(row.id),
                    business_name=business.display_name,
                    service=row.service,
                    review_status=row.review_status.value,
                    cited=tuple(sorted(cited_findings(row))),
                    reason=reason,
                    withdrawn=False,
                    skipped_audit_status=audit.status.value,
                )
            )
            continue
        pending = row.review_status is ReviewStatus.pending
        if pending and apply:
            row.withdrawn_at = moment
            row.withdrawn_reason = reason
        elif not pending:
            logger.warning(
                "opportunity no longer supported by the latest audit; not withdrawn",
                extra={
                    "business_id": str(business.id),
                    "opportunity_id": str(row.id),
                    "service": row.service,
                    "review_status": row.review_status.value,
                    "reason": reason,
                },
            )
        found.append(
            Orphan(
                opportunity_id=str(row.id),
                business_name=business.display_name,
                service=row.service,
                review_status=row.review_status.value,
                cited=tuple(sorted(cited_findings(row))),
                reason=reason,
                withdrawn=pending and apply,
            )
        )
    session.flush()
    return found


# --- repairing (v0.12.1, correction H) -----------------------------------------------------

_AUDIT_ID = re.compile(r"audit ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


@dataclass(frozen=True)
class Restorable:
    opportunity_id: str
    business_name: str
    service: str
    audit_ids: tuple[str, ...]
    audit_statuses: tuple[str, ...]
    withdrawn_reason: str
    # Why it is left withdrawn; `None` when it is (or would be) restored.
    held: str | None
    restored: bool


def find_wrongly_withdrawn(session: Session, *, apply: bool = False) -> list[Restorable]:
    """Every withdrawn row whose withdrawal named an audit that could not look.

    The audit ids come from `withdrawn_reason`, which names each one. With `apply`, the
    row's `withdrawn_at` and `withdrawn_reason` are cleared. A row is held (listed, never
    written) when its business is permanently closed, which withdraws whatever the audit
    says; when its reason names no audit that can be found; or when a live pending row
    for the same business and service already exists, which the unique index allows
    only once.
    """
    rows = list(
        session.execute(
            select(Opportunity, Business)
            .join(Business, Business.id == Opportunity.business_id)
            .where(Opportunity.withdrawn_at.is_not(None))
            .order_by(Business.display_name, Opportunity.service, Opportunity.id)
        ).tuples()
    )
    found: list[Restorable] = []
    for row, business in rows:
        reason = row.withdrawn_reason or ""
        ids = tuple(dict.fromkeys(_AUDIT_ID.findall(reason)))
        audits = [session.get(WebsiteAudit, uuid.UUID(i)) for i in ids]
        named = [a for a in audits if a is not None]
        if named and all(can_judge(row, a) for a in named):
            continue
        held: str | None = None
        if not named:
            held = "reason names no audit that exists"
        elif business.business_status is BusinessStatus.closed_permanently:
            held = "business is permanently closed"
        elif row.review_status is ReviewStatus.pending and _live_pending_exists(session, row):
            held = "a live pending row for this service already exists"
        restored = held is None and apply
        if restored:
            row.withdrawn_at = None
            row.withdrawn_reason = None
        found.append(
            Restorable(
                opportunity_id=str(row.id),
                business_name=business.display_name,
                service=row.service,
                audit_ids=ids,
                audit_statuses=tuple(a.status.value if a else "missing" for a in audits),
                withdrawn_reason=reason,
                held=held,
                restored=restored,
            )
        )
        if restored:
            logger.info(
                "wrongly withdrawn opportunity restored",
                extra={
                    "business_id": str(business.id),
                    "opportunity_id": str(row.id),
                    "service": row.service,
                    "withdrawn_reason": reason,
                },
            )
            # Flushed one at a time, so a second withdrawn row for the same service sees
            # the first one live again and is held instead of breaking the unique index.
            session.flush()
    session.flush()
    return found


def _live_pending_exists(session: Session, row: Opportunity) -> bool:
    return (
        session.scalar(
            select(Opportunity.id).where(
                Opportunity.business_id == row.business_id,
                Opportunity.service == row.service,
                Opportunity.review_status == ReviewStatus.pending,
                Opportunity.withdrawn_at.is_(None),
                Opportunity.id != row.id,
            )
        )
        is not None
    )
