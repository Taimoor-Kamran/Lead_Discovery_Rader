"""v0.12.0 not-readable status: `website_audit_status` gains `not_readable`

Revision ID: 0013_not_readable_status
Revises: 0012_bot_challenge_status
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_not_readable_status"
down_revision: str | None = "0012_bot_challenge_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("done", "skipped", "robots_blocked", "unreachable", "failed", "bot_challenge")


def upgrade() -> None:
    op.execute("ALTER TYPE website_audit_status ADD VALUE IF NOT EXISTS 'not_readable'")


def downgrade() -> None:
    # As in 0012: the enum is rebuilt without the value, and such an audit becomes `failed`
    # ("we could not read it"), never `unreachable` or `done`.
    op.execute("UPDATE website_audits SET status = 'failed' WHERE status = 'not_readable'")
    op.execute("ALTER TYPE website_audit_status RENAME TO website_audit_status_old")
    op.execute(f"CREATE TYPE website_audit_status AS ENUM ({', '.join(repr(s) for s in STATUSES)})")
    op.execute(
        "ALTER TABLE website_audits ALTER COLUMN status TYPE website_audit_status "
        "USING status::text::website_audit_status"
    )
    op.execute("DROP TYPE website_audit_status_old")
