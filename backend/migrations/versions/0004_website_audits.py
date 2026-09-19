"""v0.4.0 website audits: the website_audits table and its indexes

`job_runs.kind` is a plain string column, so the new `audit` kind needs no DDL — it is
listed in `jobs.service` alongside `discovery` and `resolution`.

Revision ID: 0004_website_audits
Revises: 0003_businesses
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_website_audits"
down_revision: str | None = "0003_businesses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WEBSITE_AUDIT_STATUS = postgresql.ENUM(
    "done",
    "skipped",
    "robots_blocked",
    "unreachable",
    "failed",
    name="website_audit_status",
    create_type=False,
)


def upgrade() -> None:
    WEBSITE_AUDIT_STATUS.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "website_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("url_audited", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("status", WEBSITE_AUDIT_STATUS, nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column(
            "checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("psi", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "tech_stack",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
        sa.Column("page_text", sa.Text(), nullable=True),
        sa.Column("html_sha256", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("content_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_website_audits_business_id_businesses"),
            ondelete="CASCADE",
        ),
        # An audit outlives the run that produced it: dropping a run must not drop the
        # evidence a salesperson is looking at.
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["job_runs.id"],
            name=op.f("fk_website_audits_job_run_id_job_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_website_audits")),
    )
    # Newest first per business: the "latest audit" lookup behind every list view.
    op.create_index(
        "ix_website_audits_business_id_created_at",
        "website_audits",
        ["business_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index("ix_website_audits_job_run_id", "website_audits", ["job_run_id"], unique=False)
    op.create_index("ix_website_audits_status", "website_audits", ["status"], unique=False)
    op.create_index(
        "ix_website_audits_content_expires_at",
        "website_audits",
        ["content_expires_at"],
        unique=False,
    )
    # GIN, for `findings @> '[{"code": "no_https"}]'`.
    op.create_index(
        "ix_website_audits_findings",
        "website_audits",
        ["findings"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_website_audits_findings", table_name="website_audits")
    op.drop_index("ix_website_audits_content_expires_at", table_name="website_audits")
    op.drop_index("ix_website_audits_status", table_name="website_audits")
    op.drop_index("ix_website_audits_job_run_id", table_name="website_audits")
    op.drop_index("ix_website_audits_business_id_created_at", table_name="website_audits")
    op.drop_table("website_audits")
    WEBSITE_AUDIT_STATUS.drop(op.get_bind(), checkfirst=True)
