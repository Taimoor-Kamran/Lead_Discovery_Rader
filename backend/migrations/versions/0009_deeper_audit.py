"""v0.11.0 deeper audit: PageSpeed accessibility and best-practices scores, listing rating

Revision ID: 0009_deeper_audit
Revises: 0008_hardening
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_deeper_audit"
down_revision: str | None = "0008_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no default: an audit made before v0.11.0, or without PageSpeed, has no
    # score, and a zero would be a claim nobody measured.
    op.add_column("website_audits", sa.Column("accessibility_score", sa.Integer(), nullable=True))
    op.add_column("website_audits", sa.Column("best_practices_score", sa.Integer(), nullable=True))
    # The listing's star rating and review count, maintained by survivorship like every
    # other business field, with provenance in `business_field_values`.
    op.add_column("businesses", sa.Column("rating", sa.Float(), nullable=True))
    op.add_column("businesses", sa.Column("user_rating_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "user_rating_count")
    op.drop_column("businesses", "rating")
    op.drop_column("website_audits", "best_practices_score")
    op.drop_column("website_audits", "accessibility_score")
