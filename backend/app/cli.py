"""Small operational commands. Run with `python -m app.cli <command> [options]`."""

import argparse
import getpass
import os
import secrets
import sys
import uuid
from collections.abc import Callable

from app import models_registry  # noqa: F401
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import configure_logging, get_logger
from app.core.security import check_jwt_secret
from app.modules.adapters import registry
from app.modules.adapters.base import DiscoveryConfig
from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
from app.modules.auth import service as auth_service
from app.modules.auth.schemas import MIN_PASSWORD_LENGTH
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
        # Printed once, to the operator's terminal only; never logged and never stored
        # in a form anything can read back.
        print(f"Generated password: {password}")
        print(
            "This is shown ONCE and cannot be recovered. Save it now — if you lose it, "
            "use `make reset-password EMAIL=...`."
        )
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
    print(f"Purged the stored payload of {purged.records} expired record(s); their IDs were kept.")
    print(
        f"Nulled {purged.field_values} expired business field value(s) and recomputed "
        f"{purged.businesses_recomputed} business(es)."
    )
    print(
        f"Nulled the stored page text of {purged.audit_page_texts} expired website audit(s); "
        "their checks, findings and evidence were kept."
    )
    return 0


def recompute_businesses(argv: list[str]) -> int:
    """Re-run survivorship for every business, or for one.

    Run this after the survivorship rules change: the values on existing rows were computed
    by the old rules and nothing else would ever revisit them. Safe to repeat — a business
    that already agrees with the rules is left alone and counted as unchanged.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli recompute-businesses")
    parser.add_argument("--business-id", help="Recompute only this business")
    args = parser.parse_args(argv)

    business_id: uuid.UUID | None = None
    if args.business_id:
        try:
            business_id = uuid.UUID(args.business_id)
        except ValueError:
            print(f"'{args.business_id}' is not a valid business id. Nothing was changed.")
            return 2

    from app.modules.resolution.survivorship import recompute_all

    with session_scope() as session:
        result = recompute_all(session, business_id=business_id)

    if result.total == 0:
        print("There are no businesses to recompute.")
        return 0
    print(
        f"Recomputed {result.total} business(es): {result.changed} changed, "
        f"{result.unchanged} unchanged."
    )
    return 0


def load_demo_data_command(argv: list[str]) -> int:
    """Load the checked-in demo fixture and run it through resolution and the audits.

    Everything it needs is checked in: the demo websites are answered from
    `app/demo/sites`, so no request leaves the machine and no API key is involved.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli load-demo-data")
    parser.add_argument(
        "--queue-only",
        action="store_true",
        help="Queue the resolution run for a worker instead of running the pipeline here",
    )
    args = parser.parse_args(argv)

    from app.demo.loader import load_demo_data, run_pipeline

    settings = get_settings()
    if not settings.is_development:
        print(
            f"APP_ENV is '{settings.environment}'. The demo fixture is fictional data and "
            "is only available in development."
        )
        return 2

    with session_scope() as session:
        try:
            result = load_demo_data(session)
        except ValidationFailedError as exc:
            print(exc.message)
            return 2

    print(
        f"Loaded {result.stored_new} new and {result.updated} existing demo record(s) "
        f"under search job {result.search_job_id}."
    )
    print(f"Discovery run:  {result.discovery_run_id}")

    if args.queue_only:
        print(f"Resolution run: {result.resolution_run_id} (queued)")
        print(
            "Watch it with GET /api/v1/jobs/<resolution run>/status, then GET /api/v1/businesses."
        )
        return 0

    pipeline = run_pipeline(result)
    print(f"Resolution run: {result.resolution_run_id} ({pipeline.resolution_status.value})")
    print(f"  {_counts(pipeline.resolution_summary)}")
    if pipeline.audit_run_id is None:
        print("No audit run was queued. Check the worker logs.")
        return 1
    status = pipeline.audit_status.value if pipeline.audit_status else "unknown"
    print(f"Audit run:      {pipeline.audit_run_id} ({status})")
    print(f"  {_counts(pipeline.audit_summary)}")
    print("Now try GET /api/v1/businesses?finding=no_online_booking.")
    return 0


def _counts(summary: dict[str, object]) -> str:
    return ", ".join(f"{key}={value}" for key, value in summary.items()) or "no counts reported"


def reset_password(argv: list[str]) -> int:
    """Set a new password for one user and invalidate every token they already hold.

    Interactive by default: the password is typed twice and never echoed, never passed on
    the command line (where it would land in shell history) and never logged.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli reset-password")
    parser.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    from_env = os.environ.get("NEW_PASSWORD")
    if from_env is not None:
        password = from_env
    else:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat new password: "):
            print("The two passwords do not match. Nothing was changed.")
            return 2

    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"A password must be at least {MIN_PASSWORD_LENGTH} characters. Nothing was changed.")
        return 2

    with session_scope() as session:
        try:
            user = auth_service.reset_password(session, args.email.strip(), password)
        except NotFoundError:
            print(f"No user has the email {args.email.strip()}. Nothing was changed.")
            return 2
        except ValidationFailedError as exc:
            print(f"{exc.message}. Nothing was changed.")
            return 2
        version = user.token_version

    print(f"Password reset for {args.email.strip()}.")
    print(f"Every existing access and refresh token for that user is now invalid (v{version}).")
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
    "recompute-businesses": recompute_businesses,
    "places-smoke": places_smoke,
    "load-demo-data": load_demo_data_command,
    "reset-password": reset_password,
}


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    check_jwt_secret()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print(f"usage: python -m app.cli [{' | '.join(COMMANDS)}] [options]")
        return 2
    return COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
