"""v0.5.0 opportunities: the ai_classifications and opportunities tables

`job_runs.kind` is a plain string column, so the new `classification` kind needs no DDL —
it is a constant in `opportunities.service` alongside `discovery`, `resolution` and `audit`.

Revision ID: 0005_opportunities
Revises: 0004_website_audits
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_opportunities"
down_revision: str | None = "0004_website_audits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLASSIFICATION_STATUS = postgresql.ENUM(
    "ok",
    "schema_invalid",
    "guardrail_trimmed",
    "error",
    "skipped_budget",
    "skipped_disabled",
    "reused",
    name="ai_classification_status",
    create_type=False,
)
OPPORTUNITY_SOURCE = postgresql.ENUM(
    "rules", "ai", "rules+ai", name="opportunity_source", create_type=False
)
REVIEW_STATUS = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    "needs_enrichment",
    "duplicate",
    "not_a_fit",
    "do_not_contact",
    name="review_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    CLASSIFICATION_STATUS.create(bind, checkfirst=True)
    OPPORTUNITY_SOURCE.create(bind, checkfirst=True)
    REVIEW_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "ai_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("website_audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("status", CLASSIFICATION_STATUS, nullable=False),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column(
            "rejected_claims",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("est_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("content_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_ai_classifications_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["website_audit_id"],
            ["website_audits.id"],
            name=op.f("fk_ai_classifications_website_audit_id_website_audits"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["job_runs.id"],
            name=op.f("fk_ai_classifications_job_run_id_job_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_classifications")),
    )
    op.create_index(
        "ix_ai_classifications_input_hash", "ai_classifications", ["input_hash"], unique=False
    )
    op.create_index(
        "ix_ai_classifications_created_at", "ai_classifications", ["created_at"], unique=False
    )
    op.create_index(
        "ix_ai_classifications_business_id", "ai_classifications", ["business_id"], unique=False
    )
    op.create_index(
        "ix_ai_classifications_content_expires_at",
        "ai_classifications",
        ["content_expires_at"],
        unique=False,
    )

    op.create_table(
        "opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("website_audit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ai_classification_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("source", OPPORTUNITY_SOURCE, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("ai_agrees", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Numeric(4, 3), nullable=False),
        sa.Column(
            "score_components",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("scoring_version", sa.Text(), nullable=False),
        sa.Column("review_status", REVIEW_STATUS, nullable=False, server_default="pending"),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_opportunities_business_id_businesses"),
            ondelete="CASCADE",
        ),
        # An opportunity outlives the audit and the classification it came from: what a
        # reviewer decided must not vanish because a run was cleaned up.
        sa.ForeignKeyConstraint(
            ["website_audit_id"],
            ["website_audits.id"],
            name=op.f("fk_opportunities_website_audit_id_website_audits"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ai_classification_id"],
            ["ai_classifications.id"],
            name=op.f("fk_opportunities_ai_classification_id_ai_classifications"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_opportunities_assigned_to_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunities")),
    )
    # One pending opportunity per business and service. Re-classification updates it.
    op.create_index(
        "uq_opportunities_pending_business_service",
        "opportunities",
        ["business_id", "service"],
        unique=True,
        postgresql_where=sa.text("review_status = 'pending'"),
    )
    # The review queue's default order: pending, best score first.
    op.create_index(
        "ix_opportunities_review_status_score",
        "opportunities",
        ["review_status", sa.text("score DESC")],
        unique=False,
    )
    op.create_index("ix_opportunities_business_id", "opportunities", ["business_id"], unique=False)
    op.create_index(
        "ix_opportunities_created_at_id", "opportunities", ["created_at", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_opportunities_created_at_id", table_name="opportunities")
    op.drop_index("ix_opportunities_business_id", table_name="opportunities")
    op.drop_index("ix_opportunities_review_status_score", table_name="opportunities")
    op.drop_index("uq_opportunities_pending_business_service", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_index("ix_ai_classifications_content_expires_at", table_name="ai_classifications")
    op.drop_index("ix_ai_classifications_business_id", table_name="ai_classifications")
    op.drop_index("ix_ai_classifications_created_at", table_name="ai_classifications")
    op.drop_index("ix_ai_classifications_input_hash", table_name="ai_classifications")
    op.drop_table("ai_classifications")
    bind = op.get_bind()
    REVIEW_STATUS.drop(bind, checkfirst=True)
    OPPORTUNITY_SOURCE.drop(bind, checkfirst=True)
    CLASSIFICATION_STATUS.drop(bind, checkfirst=True)
