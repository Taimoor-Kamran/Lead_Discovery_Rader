"""v0.3.0 entity resolution: businesses, business_field_values, match_candidates

Revision ID: 0003_businesses
Revises: 0002_discovery
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_businesses"
down_revision: str | None = "0002_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WEBSITE_KIND = postgresql.ENUM(
    "own_site",
    "builder_subdomain",
    "social_profile",
    "none",
    name="website_kind",
    create_type=False,
)
BUSINESS_STATUS = postgresql.ENUM(
    "operational",
    "closed_temporarily",
    "closed_permanently",
    "unknown",
    name="business_status",
    create_type=False,
)
MATCH_CANDIDATE_STATUS = postgresql.ENUM(
    "pending", "merged", "kept_apart", name="match_candidate_status", create_type=False
)
RESOLUTION_STATUS = postgresql.ENUM(
    "pending", "linked", "needs_review", "invalid", name="resolution_status", create_type=False
)


def upgrade() -> None:
    WEBSITE_KIND.create(op.get_bind(), checkfirst=True)
    BUSINESS_STATUS.create(op.get_bind(), checkfirst=True)
    MATCH_CANDIDATE_STATUS.create(op.get_bind(), checkfirst=True)
    RESOLUTION_STATUS.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=True),
        sa.Column("name_key", sa.Text(), nullable=True),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("address_line1", sa.Text(), nullable=True),
        sa.Column("address_line2", sa.Text(), nullable=True),
        sa.Column("street_key", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("geohash7", sa.Text(), nullable=True),
        sa.Column("phone_e164", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("website_kind", WEBSITE_KIND, nullable=False),
        sa.Column("business_status", BUSINESS_STATUS, nullable=False),
        sa.Column("places_content_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_businesses")),
    )
    # Not unique: two locations of one chain legitimately share a domain. The blueprint's
    # unique index is relaxed on purpose; the resolver sends such a pair to a human.
    op.create_index("ix_businesses_domain", "businesses", ["domain"], unique=False)
    op.create_index("ix_businesses_phone_e164", "businesses", ["phone_e164"], unique=False)
    op.create_index(
        "ix_businesses_postal_code_name_key", "businesses", ["postal_code", "name_key"], unique=False
    )
    op.create_index("ix_businesses_geohash7", "businesses", ["geohash7"], unique=False)
    op.create_index("ix_businesses_industry", "businesses", ["industry"], unique=False)
    op.create_index("ix_businesses_state_city", "businesses", ["state", "city"], unique=False)
    op.create_index("ix_businesses_created_at_id", "businesses", ["created_at", "id"], unique=False)

    op.create_table(
        "business_field_values",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("discovered_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_business_field_values_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_business_field_values_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["discovered_record_id"],
            ["discovered_records.id"],
            name=op.f("fk_business_field_values_discovered_record_id_discovered_records"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_field_values")),
        sa.UniqueConstraint(
            "business_id",
            "field",
            "discovered_record_id",
            name="uq_business_field_values_business_field_record",
        ),
    )
    op.create_index(
        "ix_business_field_values_business_id_field",
        "business_field_values",
        ["business_id", "field"],
        unique=False,
    )
    op.create_index(
        "ix_business_field_values_expires_at", "business_field_values", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_business_field_values_discovered_record_id",
        "business_field_values",
        ["discovered_record_id"],
        unique=False,
    )

    op.create_table(
        "match_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("discovered_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", MATCH_CANDIDATE_STATUS, nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discovered_record_id"],
            ["discovered_records.id"],
            name=op.f("fk_match_candidates_discovered_record_id_discovered_records"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_match_candidates_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_match_candidates_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_candidates")),
        sa.UniqueConstraint(
            "discovered_record_id", "business_id", name="uq_match_candidates_record_business"
        ),
    )
    op.create_index("ix_match_candidates_status", "match_candidates", ["status"], unique=False)
    op.create_index(
        "ix_match_candidates_created_at_id", "match_candidates", ["created_at", "id"], unique=False
    )

    # Every token carries the version it was issued under; `reset-password` raises it,
    # which retires every token that user already holds.
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
    )

    # `job_runs.kind` is a plain string, so the new `resolution` kind needs no change.
    # `params` is what a run was asked to do: a resolution run carries its parent run id.
    op.add_column(
        "job_runs",
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.add_column(
        "discovered_records",
        sa.Column(
            "resolution_status",
            RESOLUTION_STATUS,
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("discovered_records", sa.Column("resolution_error", sa.Text(), nullable=True))
    op.add_column(
        "discovered_records", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_discovered_records_business_id_businesses"),
        "discovered_records",
        "businesses",
        ["business_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_discovered_records_resolution_status",
        "discovered_records",
        ["resolution_status"],
        unique=False,
    )
    op.create_index(
        "ix_discovered_records_business_id", "discovered_records", ["business_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_discovered_records_business_id", table_name="discovered_records")
    op.drop_index("ix_discovered_records_resolution_status", table_name="discovered_records")
    op.drop_constraint(
        op.f("fk_discovered_records_business_id_businesses"),
        "discovered_records",
        type_="foreignkey",
    )
    op.drop_column("discovered_records", "resolved_at")
    op.drop_column("discovered_records", "resolution_error")
    op.drop_column("discovered_records", "resolution_status")
    op.drop_column("job_runs", "params")
    op.drop_column("users", "token_version")

    op.drop_index("ix_match_candidates_created_at_id", table_name="match_candidates")
    op.drop_index("ix_match_candidates_status", table_name="match_candidates")
    op.drop_table("match_candidates")

    op.drop_index(
        "ix_business_field_values_discovered_record_id", table_name="business_field_values"
    )
    op.drop_index("ix_business_field_values_expires_at", table_name="business_field_values")
    op.drop_index("ix_business_field_values_business_id_field", table_name="business_field_values")
    op.drop_table("business_field_values")

    op.drop_index("ix_businesses_created_at_id", table_name="businesses")
    op.drop_index("ix_businesses_state_city", table_name="businesses")
    op.drop_index("ix_businesses_industry", table_name="businesses")
    op.drop_index("ix_businesses_geohash7", table_name="businesses")
    op.drop_index("ix_businesses_postal_code_name_key", table_name="businesses")
    op.drop_index("ix_businesses_phone_e164", table_name="businesses")
    op.drop_index("ix_businesses_domain", table_name="businesses")
    op.drop_table("businesses")

    RESOLUTION_STATUS.drop(op.get_bind(), checkfirst=True)
    MATCH_CANDIDATE_STATUS.drop(op.get_bind(), checkfirst=True)
    BUSINESS_STATUS.drop(op.get_bind(), checkfirst=True)
    WEBSITE_KIND.drop(op.get_bind(), checkfirst=True)
