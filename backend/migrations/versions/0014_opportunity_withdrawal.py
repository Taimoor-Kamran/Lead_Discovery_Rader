"""v0.12.1 withdrawal: `opportunities.withdrawn_at` / `withdrawn_reason`

A pending opportunity whose audit stopped supporting it is withdrawn by the system, not
decided by a person, so it gets its own columns rather than a seventh review status. The
one-pending-row-per-service index now ignores withdrawn rows, so a withdrawn row never
stands in the way of the service being opened again.

Revision ID: 0014_opportunity_withdrawal
Revises: 0013_not_readable_status
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_opportunity_withdrawal"
down_revision: str | None = "0013_not_readable_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_opportunities_pending_business_service"


def upgrade() -> None:
    op.add_column(
        "opportunities", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("opportunities", sa.Column("withdrawn_reason", sa.Text(), nullable=True))
    op.drop_index(INDEX, table_name="opportunities")
    op.create_index(
        INDEX,
        "opportunities",
        ["business_id", "service"],
        unique=True,
        postgresql_where=sa.text("review_status = 'pending' AND withdrawn_at IS NULL"),
    )


def downgrade() -> None:
    # The old index allows one pending row per service, withdrawn or not. A withdrawn row
    # that has a live pending sibling is a claim the system already took back, so it is
    # the one that goes; any other withdrawn row simply becomes pending again.
    op.execute(
        """
        DELETE FROM opportunities w
        WHERE w.review_status = 'pending' AND w.withdrawn_at IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM opportunities o
            WHERE o.business_id = w.business_id AND o.service = w.service
              AND o.review_status = 'pending' AND o.withdrawn_at IS NULL
          )
        """
    )
    op.drop_index(INDEX, table_name="opportunities")
    op.create_index(
        INDEX,
        "opportunities",
        ["business_id", "service"],
        unique=True,
        postgresql_where=sa.text("review_status = 'pending'"),
    )
    op.drop_column("opportunities", "withdrawn_reason")
    op.drop_column("opportunities", "withdrawn_at")
