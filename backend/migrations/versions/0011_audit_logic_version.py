"""v0.12.0 audit logic version: `website_audits.audit_logic_version`, compared by `needs_audit`

Revision ID: 0011_audit_logic_version
Revises: 0010_job_run_heartbeat
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_audit_logic_version"
down_revision: str | None = "0010_job_run_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "website_audits",
        sa.Column("audit_logic_version", sa.Integer(), nullable=False, server_default="0"),
    )
    # Existing rows carry the version they were written by as text, `audit-3`. That number
    # is the logic version; a row whose text says otherwise stays 0, which is older than
    # any real version and so makes its business due for a fresh audit.
    op.execute(
        "UPDATE website_audits "
        "SET audit_logic_version = CAST(substring(rules_version FROM '^audit-([0-9]+)$') AS integer) "
        "WHERE rules_version ~ '^audit-[0-9]+$'"
    )


def downgrade() -> None:
    op.drop_column("website_audits", "audit_logic_version")
