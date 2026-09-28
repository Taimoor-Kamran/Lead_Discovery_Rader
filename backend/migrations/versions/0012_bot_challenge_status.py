"""v0.12.0 bot-challenge status: `website_audit_status` gains `bot_challenge`

Revision ID: 0012_bot_challenge_status
Revises: 0011_audit_logic_version
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_bot_challenge_status"
down_revision: str | None = "0011_audit_logic_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("done", "skipped", "robots_blocked", "unreachable", "failed")


def upgrade() -> None:
    op.execute("ALTER TYPE website_audit_status ADD VALUE IF NOT EXISTS 'bot_challenge'")


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value, so the type is rebuilt without it. A challenged
    # audit becomes `failed` — "we could not read it", which stays true — never `unreachable`,
    # which would say the site is down, and `failed` is due again soon under the old code.
    op.execute("UPDATE website_audits SET status = 'failed' WHERE status = 'bot_challenge'")
    op.execute("ALTER TYPE website_audit_status RENAME TO website_audit_status_old")
    op.execute(f"CREATE TYPE website_audit_status AS ENUM ({', '.join(repr(s) for s in STATUSES)})")
    op.execute(
        "ALTER TABLE website_audits ALTER COLUMN status TYPE website_audit_status "
        "USING status::text::website_audit_status"
    )
    op.execute("DROP TYPE website_audit_status_old")
