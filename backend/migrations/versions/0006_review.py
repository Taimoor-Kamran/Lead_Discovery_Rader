"""v0.6.0 review: review_decisions, suppressions, and the decision columns on opportunities

Revision ID: 0006_review
Revises: 0005_opportunities
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_review"
down_revision: str | None = "0005_opportunities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVIEW_DECISION = postgresql.ENUM(
    "approve",
    "reject",
    "needs_enrichment",
    "duplicate",
    "not_a_fit",
    "do_not_contact",
    name="review_decision",
    create_type=False,
)
SUPPRESSION_SOURCE = postgresql.ENUM(
    "review", "admin", name="suppression_source", create_type=False
)
# Already exists from 0005; referenced here for the two status columns.
REVIEW_STATUS = postgresql.ENUM(name="review_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    REVIEW_DECISION.create(bind, checkfirst=True)
    SUPPRESSION_SOURCE.create(bind, checkfirst=True)

    op.add_column(
        "opportunities", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "opportunities", sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "opportunities",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        op.f("fk_opportunities_decided_by_users"),
        "opportunities",
        "users",
        ["decided_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "review_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", REVIEW_DECISION, nullable=False),
        sa.Column("from_status", REVIEW_STATUS, nullable=False),
        sa.Column("to_status", REVIEW_STATUS, nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("duplicate_of", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_review_decisions_opportunity_id_opportunities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of"],
            ["opportunities.id"],
            name=op.f("fk_review_decisions_duplicate_of_opportunities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_review_decisions_assigned_to_users"),
            ondelete="SET NULL",
        ),
        # A decision is a record of a person's act: the person cannot be deleted under it.
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_review_decisions_decided_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["undone_by"],
            ["users.id"],
            name=op.f("fk_review_decisions_undone_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_decisions")),
    )
    op.create_index(
        "ix_review_decisions_opportunity_id", "review_decisions", ["opportunity_id"], unique=False
    )
    op.create_index(
        "ix_review_decisions_decided_at_id",
        "review_decisions",
        ["decided_at", "id"],
        unique=False,
    )

    op.create_table(
        "suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("phone_e164", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", SUPPRESSION_SOURCE, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lifted_by", postgresql.UUID(as_uuid=True), nullable=True),
        # A suppression by domain or phone must outlive the business row it was made
        # from: the whole point is to catch the business when it comes back.
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_suppressions_business_id_businesses"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_suppressions_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lifted_by"],
            ["users.id"],
            name=op.f("fk_suppressions_lifted_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppressions")),
    )
    # Active rows only: a lifted suppression is history, not a filter.
    op.create_index(
        "ix_suppressions_active_domain",
        "suppressions",
        ["domain"],
        unique=False,
        postgresql_where=sa.text("lifted_at IS NULL"),
    )
    op.create_index(
        "ix_suppressions_active_phone_e164",
        "suppressions",
        ["phone_e164"],
        unique=False,
        postgresql_where=sa.text("lifted_at IS NULL"),
    )
    op.create_index(
        "ix_suppressions_active_business_id",
        "suppressions",
        ["business_id"],
        unique=False,
        postgresql_where=sa.text("lifted_at IS NULL"),
    )
    op.create_index(
        "ix_suppressions_created_at_id", "suppressions", ["created_at", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_suppressions_created_at_id", table_name="suppressions")
    op.drop_index("ix_suppressions_active_business_id", table_name="suppressions")
    op.drop_index("ix_suppressions_active_phone_e164", table_name="suppressions")
    op.drop_index("ix_suppressions_active_domain", table_name="suppressions")
    op.drop_table("suppressions")
    op.drop_index("ix_review_decisions_decided_at_id", table_name="review_decisions")
    op.drop_index("ix_review_decisions_opportunity_id", table_name="review_decisions")
    op.drop_table("review_decisions")
    op.drop_constraint(
        op.f("fk_opportunities_decided_by_users"), "opportunities", type_="foreignkey"
    )
    op.drop_column("opportunities", "lock_version")
    op.drop_column("opportunities", "decided_by")
    op.drop_column("opportunities", "decided_at")
    bind = op.get_bind()
    SUPPRESSION_SOURCE.drop(bind, checkfirst=True)
    REVIEW_DECISION.drop(bind, checkfirst=True)
