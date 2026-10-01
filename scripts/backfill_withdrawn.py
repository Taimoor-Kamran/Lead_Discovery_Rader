"""Withdraw the pending opportunities orphaned before v0.12.1. Run by a human, once.

Dry run (the default) prints every orphan and writes nothing:

    uv run --project backend python scripts/backfill_withdrawn.py

Against production, inside the api container (the image does not ship `scripts/`):

    docker compose -p radar-prod --env-file .env.prod -f docker-compose.yml \\
        -f docker-compose.prod.yml exec -T api python - < scripts/backfill_withdrawn.py
    ... exec -T api python - --apply < scripts/backfill_withdrawn.py

An orphan is a row that cites at least one finding, none of which is in its business's
latest audit. Only `pending` rows are written, and only with `--apply`; an `approved` or
`needs_enrichment` row in the same situation is listed as "kept" and never touched.

A row whose business's latest audit is not `done` or `skipped` (it failed, or the site was
unreachable, refused us, or could not be read) is listed as "skipped" and never touched:
an audit that could not look is no evidence a finding is gone. The last line counts them.
"""

import argparse
import sys

from app import models_registry  # noqa: F401
from app.core.db import session_scope
from app.modules.opportunities.backfill import Orphan, find_orphans


def render(orphans: list[Orphan], *, apply: bool) -> list[str]:
    lines = []
    for orphan in orphans:
        if orphan.skipped_audit_status is not None:
            action = f"skipped (audit {orphan.skipped_audit_status})"
        elif orphan.review_status != "pending":
            action = f"kept ({orphan.review_status})"
        else:
            action = "WITHDRAWN" if orphan.withdrawn else "would withdraw"
        lines.append(
            f"{action}\t{orphan.business_name}\t{orphan.service}\t"
            f"cites {', '.join(orphan.cited)}\t{orphan.opportunity_id}"
        )
    skipped = sum(1 for o in orphans if o.skipped_audit_status is not None)
    pending = sum(
        1 for o in orphans if o.skipped_audit_status is None and o.review_status == "pending"
    )
    kept = len(orphans) - pending - skipped
    verb = "withdrawn" if apply else "to withdraw (dry run; pass --apply to write)"
    lines.append(
        f"{pending} pending {verb}; {kept} not pending, kept and logged; "
        f"{skipped} skipped: latest audit is not done or skipped"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write withdrawn_at (default: dry)")
    args = parser.parse_args(argv)
    with session_scope() as session:
        orphans = find_orphans(session, apply=args.apply)
        if not args.apply:
            session.rollback()
        for line in render(orphans, apply=args.apply):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
