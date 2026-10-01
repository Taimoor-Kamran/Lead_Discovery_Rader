"""Restore the opportunities withdrawn against an audit that could not look. Run once.

v0.12.1, correction H. Before it, a `failed`, `unreachable`, `bot_challenge`,
`not_readable` or `robots_blocked` audit (no findings because nothing was read) withdrew
every pending row it was compared with. This finds each withdrawn row whose
`withdrawn_reason` names such an audit and clears `withdrawn_at` and `withdrawn_reason`.

Dry run (the default) prints every row and writes nothing:

    uv run --project backend python scripts/repair_wrongly_withdrawn.py

Against production, inside the api container (the image does not ship `scripts/`):

    docker compose -p radar-prod --env-file .env.prod -f docker-compose.yml \\
        -f docker-compose.prod.yml exec -T api python - < scripts/repair_wrongly_withdrawn.py
    ... exec -T api python - --apply < scripts/repair_wrongly_withdrawn.py

A row is "held" (listed, never written) when its business is permanently closed, when its
reason names no audit that exists, or when a live pending row for the same business and
service already exists.
"""

import argparse
import sys

from app import models_registry  # noqa: F401
from app.core.db import session_scope
from app.modules.opportunities.backfill import Restorable, find_wrongly_withdrawn


def render(rows: list[Restorable], *, apply: bool) -> list[str]:
    lines = []
    for row in rows:
        if row.held is not None:
            action = f"held ({row.held})"
        else:
            action = "RESTORED" if row.restored else "would restore"
        audits = ", ".join(
            f"{i} ({s})" for i, s in zip(row.audit_ids, row.audit_statuses, strict=True)
        )
        lines.append(
            f"{action}\t{row.business_name}\t{row.service}\t"
            f"audit {audits or '-'}\t{row.opportunity_id}"
        )
    held = sum(1 for r in rows if r.held is not None)
    ready = len(rows) - held
    verb = "restored" if apply else "to restore (dry run; pass --apply to write)"
    lines.append(f"{ready} {verb}; {held} held")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="clear withdrawn_at (default: dry)")
    args = parser.parse_args(argv)
    with session_scope() as session:
        rows = find_wrongly_withdrawn(session, apply=args.apply)
        if not args.apply:
            session.rollback()
        for line in render(rows, apply=args.apply):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
