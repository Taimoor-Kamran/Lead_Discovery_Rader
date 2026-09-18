"""v0.1.0 foundation: users, sources, search_jobs, job_runs, audit_logs

Revision ID: 0001_foundation
Revises:
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_ROLE = postgresql.ENUM(
    "admin",
    "sales_rep",
    "reviewer",
    "tech_admin",
    "crm_manager",
    name="user_role",
    create_type=False,
)
SOURCE_KIND = postgresql.ENUM(
    "api", "web", "feed", "licensed", name="source_kind", create_type=False
)
SEARCH_JOB_STATUS = postgresql.ENUM(
    "draft", "active", "archived", name="search_job_status", create_type=False
)
JOB_RUN_STATUS = postgresql.ENUM(
    "queued", "running", "done", "failed", "cancelled", name="job_run_status", create_type=False
)

# audit_logs is append-only. A trigger enforces it for every role, including the table
# owner, which plain GRANTs would not cover.
AUDIT_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    USER_ROLE.create(op.get_bind(), checkfirst=True)
    SOURCE_KIND.create(op.get_bind(), checkfirst=True)
    SEARCH_JOB_STATUS.create(op.get_bind(), checkfirst=True)
    JOB_RUN_STATUS.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", USER_ROLE, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", SOURCE_KIND, nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("name", name=op.f("uq_sources_name")),
    )

    op.create_table(
        "search_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("geo", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("industry", sa.String(length=120), nullable=False),
        sa.Column("source_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False),
        sa.Column("status", SEARCH_JOB_STATUS, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_search_jobs_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_jobs")),
    )
    op.create_index(
        "ix_search_jobs_created_at_id", "search_jobs", ["created_at", "id"], unique=False
    )

    op.create_table(
        "job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("search_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", JOB_RUN_STATUS, nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("progress_done", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["search_job_id"],
            ["search_jobs.id"],
            name=op.f("fk_job_runs_search_job_id_search_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_job_runs_idempotency_key")),
    )
    op.create_index(op.f("ix_job_runs_status"), "job_runs", ["status"], unique=False)
    op.create_index("ix_job_runs_search_job_id", "job_runs", ["search_job_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(
        "ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"], unique=False
    )

    op.execute(AUDIT_GUARD_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_logs_no_update BEFORE UPDATE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_delete BEFORE DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs")
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_append_only()")

    op.drop_index("ix_job_runs_search_job_id", table_name="job_runs")
    op.drop_index(op.f("ix_job_runs_status"), table_name="job_runs")
    op.drop_table("job_runs")

    op.drop_index("ix_search_jobs_created_at_id", table_name="search_jobs")
    op.drop_table("search_jobs")
    op.drop_table("sources")
    op.drop_table("users")

    JOB_RUN_STATUS.drop(op.get_bind(), checkfirst=True)
    SEARCH_JOB_STATUS.drop(op.get_bind(), checkfirst=True)
    SOURCE_KIND.drop(op.get_bind(), checkfirst=True)
    USER_ROLE.drop(op.get_bind(), checkfirst=True)
