"""v0.11.0 job run heartbeat: `job_runs.updated_at`, so the watchdog judges progress

Revision ID: 0010_job_run_heartbeat
Revises: 0009_deeper_audit
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_job_run_heartbeat"
down_revision: str | None = "0009_deeper_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows take the column default, `now()`: for a finished run it is never read,
    # and a run left `running` gets the full stale-run limit from the upgrade, not from a
    # start time that may be long past.
    op.add_column(
        "job_runs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("job_runs", "updated_at")
