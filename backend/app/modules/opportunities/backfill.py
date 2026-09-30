"""v0.12.1 one-off: withdraw the pending opportunities orphaned before withdrawal existed.

Run by a human through `scripts/backfill_withdrawn.py`, dry run by default. There is no
merged result to compare with here, so only the cited-findings test is applied: a pending
row that cites at least one finding, none of which is in its business's latest audit. A
row that cites nothing (`ads_social`) is never listed (correction C).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.audit_web.service import latest_audits
from app.modules.businesses.models import Business
from app.modules.opportunities.models import Opportunity, ReviewStatus
from app.modules.opportunities.service import OPEN_STATUSES, cited_findings, withdrawal_reasons

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
