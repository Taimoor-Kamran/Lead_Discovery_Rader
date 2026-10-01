"""v0.13.0 domain intelligence: the `domain_intel` cache table

One row per registrable domain: the DNS snapshot and the RDAP snapshot (JSONB), each with
its own checked-at time. A cache only — every audit copies what it used into its own
`checks`, so dropping this table loses no evidence.

Revision ID: 0015_domain_intel
Revises: 0014_opportunity_withdrawal
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_domain_intel"
down_revision: str | None = "0014_opportunity_withdrawal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_intel",
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("dns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rdap", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dns_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rdap_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("domain", name=op.f("pk_domain_intel")),
    )


def downgrade() -> None:
    op.drop_table("domain_intel")
