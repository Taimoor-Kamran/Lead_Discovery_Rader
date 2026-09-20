"""v0.7.0 CRM export: crm_leads, crm_lead_opportunities, crm_sync_attempts, crm_fake_records

Revision ID: 0007_crm
Revises: 0006_review
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_crm"
down_revision: str | None = "0006_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CRM_LEAD_STATUS = postgresql.ENUM(
    "scheduled",
    "syncing",
    "synced",
    "held",
    "cancelled",
    "withdrawn",
    name="crm_lead_status",
    create_type=False,
)
CRM_SYNC_ACTION = postgresql.ENUM(
    "create",
    "update",
    "link",
    "unchanged",
    "mark_dnc",
    "withdraw",
    "export",
    name="crm_sync_action",
    create_type=False,
)
CRM_SYNC_STATUS = postgresql.ENUM("ok", "failed", name="crm_sync_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    CRM_LEAD_STATUS.create(bind, checkfirst=True)
    CRM_SYNC_ACTION.create(bind, checkfirst=True)
    CRM_SYNC_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "crm_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column("status", CRM_LEAD_STATUS, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_batch_id", sa.Text(), nullable=True),
        sa.Column("do_not_contact_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_crm_leads_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_leads")),
        sa.UniqueConstraint("business_id", "destination", name="uq_crm_leads_business_destination"),
    )
    op.create_index("ix_crm_leads_status_due_at", "crm_leads", ["status", "due_at"], unique=False)
    op.create_index(
        "ix_crm_leads_destination_status", "crm_leads", ["destination", "status"], unique=False
    )

    op.create_table(
        "crm_lead_opportunities",
        sa.Column("crm_lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["crm_lead_id"],
            ["crm_leads.id"],
            name=op.f("fk_crm_lead_opportunities_crm_lead_id_crm_leads"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_crm_lead_opportunities_opportunity_id_opportunities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "crm_lead_id", "opportunity_id", name=op.f("pk_crm_lead_opportunities")
        ),
    )

    op.create_table(
        "crm_sync_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("crm_lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", CRM_SYNC_ACTION, nullable=False),
        sa.Column("status", CRM_SYNC_STATUS, nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["crm_lead_id"],
            ["crm_leads.id"],
            name=op.f("fk_crm_sync_attempts_crm_lead_id_crm_leads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_sync_attempts")),
    )
    op.create_index(
        "ix_crm_sync_attempts_crm_lead_id_id",
        "crm_sync_attempts",
        ["crm_lead_id", "id"],
        unique=False,
    )

    # Development and tests only: the fake destination's store. Created everywhere so the
    # schema is the same in every environment; it simply stays empty in production.
    op.create_table(
        "crm_fake_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_fake_records")),
    )


def downgrade() -> None:
    op.drop_table("crm_fake_records")
    op.drop_index("ix_crm_sync_attempts_crm_lead_id_id", table_name="crm_sync_attempts")
    op.drop_table("crm_sync_attempts")
    op.drop_table("crm_lead_opportunities")
    op.drop_index("ix_crm_leads_destination_status", table_name="crm_leads")
    op.drop_index("ix_crm_leads_status_due_at", table_name="crm_leads")
    op.drop_table("crm_leads")
    bind = op.get_bind()
    CRM_SYNC_STATUS.drop(bind, checkfirst=True)
    CRM_SYNC_ACTION.drop(bind, checkfirst=True)
    CRM_LEAD_STATUS.drop(bind, checkfirst=True)
