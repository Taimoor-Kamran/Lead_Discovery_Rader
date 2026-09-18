"""v0.2.0 discovery: discovered_records, record_sightings, api_calls, job_runs.result_summary

Revision ID: 0002_discovery
Revises: 0001_foundation
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_discovery"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_runs",
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table(
        "discovered_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        # The foreign key to `businesses` is added in v0.3.0, with the table.
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("content_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_discovered_records_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovered_records")),
        sa.UniqueConstraint(
            "source_id", "source_record_id", name="uq_discovered_records_source_key"
        ),
    )
    op.create_index(
        "ix_discovered_records_content_expires_at",
        "discovered_records",
        ["content_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_discovered_records_created_at_id",
        "discovered_records",
        ["created_at", "id"],
        unique=False,
    )

    op.create_table(
        "record_sightings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("discovered_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("search_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discovered_record_id"],
            ["discovered_records.id"],
            name=op.f("fk_record_sightings_discovered_record_id_discovered_records"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["job_runs.id"],
            name=op.f("fk_record_sightings_job_run_id_job_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["search_job_id"],
            ["search_jobs.id"],
            name=op.f("fk_record_sightings_search_job_id_search_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_record_sightings")),
        sa.UniqueConstraint(
            "discovered_record_id", "job_run_id", name="uq_record_sightings_record_run"
        ),
    )
    op.create_index(
        "ix_record_sightings_job_run_id", "record_sightings", ["job_run_id"], unique=False
    )

    op.create_table(
        "api_calls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error_class", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_api_calls_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["job_runs.id"],
            name=op.f("fk_api_calls_job_run_id_job_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_calls")),
    )
    op.create_index(
        "ix_api_calls_source_id_created_at", "api_calls", ["source_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_api_calls_source_id_created_at", table_name="api_calls")
    op.drop_table("api_calls")

    op.drop_index("ix_record_sightings_job_run_id", table_name="record_sightings")
    op.drop_table("record_sightings")

    op.drop_index("ix_discovered_records_created_at_id", table_name="discovered_records")
    op.drop_index("ix_discovered_records_content_expires_at", table_name="discovered_records")
    op.drop_table("discovered_records")

    op.drop_column("job_runs", "result_summary")
