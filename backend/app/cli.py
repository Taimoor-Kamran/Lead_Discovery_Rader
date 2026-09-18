"""Small operational commands. Run with `python -m app.cli <command> [options]`."""

import argparse
import os
import secrets
import sys
import uuid
from collections.abc import Callable

from app import models_registry  # noqa: F401
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.modules.adapters import registry
from app.modules.adapters.base import DiscoveryConfig
from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
from app.modules.auth.service import ensure_admin
from app.modules.discovery.service import purge_expired
from app.modules.jobs.schemas import GeoSpec

logger = get_logger("app.cli")

GENERATED_PASSWORD_BYTES = 18
SMOKE_COLUMNS = ("name", "address", "phone", "website")


def seed_admin(argv: list[str]) -> int:
    """Create (or promote) the bootstrap admin from ADMIN_EMAIL / ADMIN_PASSWORD."""
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    if not email:
        print("ADMIN_EMAIL is not set. Set it in .env or pass it on the command line.")
        return 2

    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    generated = False
    if not password:
        password = secrets.token_urlsafe(GENERATED_PASSWORD_BYTES)
        generated = True

    with session_scope() as session:
        user, created = ensure_admin(session, email, password)
        user_id = str(user.id)

    action = "created" if created else "promoted to admin"
    print(f"Admin {email} {action} (id {user_id}).")
    if generated and created:
        # Printed once, to the operator's terminal only; never logged.
        print(f"Generated password: {password}")
    elif not created:
        print("Existing user kept its current password.")
    return 0


def sync_sources(argv: list[str]) -> int:
    """Upsert one `sources` row per registered adapter. Safe to run on every deploy."""
    with session_scope() as session:
        sources = registry.sync_sources(session)
        lines = [f"  {s.name:<20} kind={s.kind.value:<9} enabled={s.enabled}" for s in sources]
    print(f"Synced {len(lines)} source(s):")
    print("\n".join(lines))
    return 0


def purge_expired_command(argv: list[str]) -> int:
    """Drop stored source content whose retention window has closed. Place IDs are kept."""
    with session_scope() as session:
        purged = purge_expired(session)
    print(f"Purged the stored payload of {purged} expired record(s); their IDs were kept.")
    return 0


def places_smoke(argv: list[str]) -> int:
    """Make one real Google Places call and print what came back. Stores nothing.

    This is the only command in the repository that talks to a live external API, and a
    human has to run it. It costs money — check the budget alert first.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli places-smoke")
    parser.add_argument("--industry", required=True)
    parser.add_argument("--city")
    parser.add_argument("--state")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lng", type=float)
    parser.add_argument("--radius-m", type=int)
    parser.add_argument("--max", type=int, default=5, dest="max_results")
    args = parser.parse_args(argv)

    if not get_settings().google_places_api_key.get_secret_value():
        print("GOOGLE_PLACES_API_KEY is not set. Put a restricted key in .env first.")
        return 2

    try:
        geo = GeoSpec(
            city=args.city,
            state=args.state,
            lat=args.lat,
            lng=args.lng,
            radius_m=args.radius_m,
        )
    except ValueError as exc:
        print(f"Bad search area: {exc}")
        return 2

    adapter = registry.get(GOOGLE_PLACES)
    cfg = DiscoveryConfig(
        industry=args.industry,
        geo=geo,
        # A smoke run stores nothing, so there is no job run to meter against.
        job_run_id=uuid.uuid4(),
        max_results=max(args.max_results, 1),
    )

    rows: list[tuple[str, ...]] = []
    for ref in adapter.discover(cfg):
        raw = adapter.fetch(ref)
        if not adapter.validate(raw).valid:
            continue
        candidate = adapter.normalize(raw)
        rows.append(
            (
                candidate.display_name or "-",
                candidate.formatted_address or "-",
                candidate.phone or "-",
                candidate.website or "-",
            )
        )

    if not rows:
        print("The API answered, but no results matched. Nothing was stored.")
        return 0

    widths = [
        max(len(header), *(len(row[i]) for row in rows)) for i, header in enumerate(SMOKE_COLUMNS)
    ]
    print(" | ".join(h.upper().ljust(w) for h, w in zip(SMOKE_COLUMNS, widths, strict=True)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)))
    print(f"\n{len(rows)} result(s). Nothing was stored.")
    return 0


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "seed-admin": seed_admin,
    "sync-sources": sync_sources,
    "purge-expired": purge_expired_command,
    "places-smoke": places_smoke,
}


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print(f"usage: python -m app.cli [{' | '.join(COMMANDS)}] [options]")
        return 2
    return COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
