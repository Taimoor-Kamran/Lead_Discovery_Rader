"""Small operational commands. Run with `python -m app.cli <command> [options]`."""

import argparse
import getpass
import os
import secrets
import sys
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app import models_registry  # noqa: F401
from app.core.backup import BackupResult
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import configure_logging, get_logger
from app.core.startup import check_startup, refuse_outside_development
from app.modules.adapters import registry
from app.modules.adapters.base import DiscoveryConfig
from app.modules.adapters.google_places.adapter import SOURCE_NAME as GOOGLE_PLACES
from app.modules.ai.client import Tier
from app.modules.auth import service as auth_service
from app.modules.auth.models import Role
from app.modules.auth.schemas import MIN_PASSWORD_LENGTH
from app.modules.auth.service import ensure_admin, ensure_user
from app.modules.discovery.service import purge_expired
from app.modules.jobs.models import JobRun, JobRunStatus
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
        user, created = ensure_admin(session, email, password, must_change_password=generated)
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


# The people the manual test plan signs in as. Fictional addresses under example.com.
DEMO_USERS: tuple[tuple[str, Role], ...] = (
    ("reviewer@example.com", Role.reviewer),
    ("rep1@example.com", Role.sales_rep),
    ("rep2@example.com", Role.sales_rep),
    ("crm@example.com", Role.crm_manager),
)


def seed_demo_users(argv: list[str]) -> int:
    """Create the demo reviewer, two sales reps and a CRM manager. Development only.

    The password comes from DEMO_USERS_PASSWORD and is never printed. Running it again
    is safe: an existing user keeps its password and is only given the demo role.
    """
    settings = get_settings()
    refusal = refuse_outside_development(settings, "seed-demo-users")
    if refusal is not None:
        print(f"{refusal} Create real users from the Users page or POST /api/v1/users.")
        return 2
    password = settings.demo_users_password.get_secret_value().strip()
    if not password:
        print("DEMO_USERS_PASSWORD is not set. Put one in .env first (12+ characters).")
        return 2
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"DEMO_USERS_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters.")
        return 2

    lines: list[str] = []
    with session_scope() as session:
        for email, role in DEMO_USERS:
            _, created = ensure_user(session, email, password, role)
            state = "created" if created else "already existed (password kept)"
            lines.append(f"  {email:<24} {role.value:<12} {state}")
    print(f"Demo users ({len(lines)}):")
    print("\n".join(lines))
    print("They all sign in with DEMO_USERS_PASSWORD.")
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
    print(
        f"Nulled the raw model output and summary of {purged.ai_classifications} expired AI "
        "classification(s); their validated opportunities and rejected claims were kept."
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
    """Load the checked-in demo fixture and run it through resolution, audits and scoring.

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
    refusal = refuse_outside_development(settings, "load-demo-data")
    if refusal is not None:
        print(f"{refusal} The demo fixture is fictional data.")
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
    if pipeline.classification_run_id is None:
        print("No classification run was queued. Check the worker logs.")
        return 1
    status = pipeline.classification_status.value if pipeline.classification_status else "unknown"
    print(f"Classification: {pipeline.classification_run_id} ({status})")
    print(f"  {_counts(pipeline.classification_summary)}")
    print(f"  AI provider: {settings.resolved_ai_provider} (no network call was made)")
    print("Now try GET /api/v1/opportunities.")
    return 0


def reset_demo_data_command(argv: list[str]) -> int:
    """Put the demo back to freshly loaded: no decisions, no suppressions, all pending.

    `make e2e` runs this first so the smoke test never depends on what a human clicked.
    Development only; nothing here touches a network.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli reset-demo-data")
    parser.parse_args(argv)

    from app.demo.loader import reset_demo_data

    settings = get_settings()
    refusal = refuse_outside_development(settings, "reset-demo-data")
    if refusal is not None:
        print(refusal)
        return 2

    with session_scope() as session:
        try:
            result = reset_demo_data(session)
        except ValidationFailedError as exc:
            print(exc.message)
            return 2

    print(
        f"Removed {result.decisions_deleted} decision(s), {result.suppressions_deleted} "
        f"suppression(s) and {result.opportunities_deleted} opportunit(y/ies)."
    )
    status = result.classification_status.value if result.classification_status else "unknown"
    print(f"Classification: {result.classification_run_id} ({status})")
    print(f"  {_counts(result.classification_summary)}")
    return 0 if result.classification_status is JobRunStatus.done else 1


def _counts(summary: dict[str, object]) -> str:
    return ", ".join(f"{key}={value}" for key, value in summary.items()) or "no counts reported"


def crm_check(argv: list[str]) -> int:
    """`make crm-check`: is the CRM destination set up? Prints one OK/missing line per check.

    For Airtable this needs `schema.bases:read` on the token. Nothing is written. Exit 0 when
    everything is fine, 1 when a check failed, 2 when the destination is not configured.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli crm-check")
    parser.parse_args(argv)

    from app.modules.crm import adapter as crm_adapters
    from app.modules.crm.adapter import CrmError

    settings = get_settings()
    print(f"Destination: {settings.crm_destination}")
    with session_scope() as session:
        try:
            health = crm_adapters.build(session, settings=settings).check()
        except CrmError as exc:
            print(f"  FAIL  {exc.message}")
            return 2
    width = max((len(check.name) for check in health.checks), default=10)
    for check in health.checks:
        print(
            f"  {'OK   ' if check.ok else 'MISSING' if 'missing' in check.detail else 'FAIL '}"
            f" {check.name:<{width}}  {check.detail}"
        )
    print(health.message or ("all checks passed" if health.ok else "some checks failed"))
    return 0 if health.ok else 1


def crm_bootstrap_airtable(argv: list[str]) -> int:
    """`make crm-bootstrap-airtable`: create the Leads table with every field. Needs
    `schema.bases:write`. Refuses when the table already exists."""
    parser = argparse.ArgumentParser(prog="python -m app.cli crm-bootstrap-airtable")
    parser.parse_args(argv)

    from app.modules.crm.adapter import CrmError
    from app.modules.crm.airtable_adapter import build_airtable_adapter

    settings = get_settings()
    with session_scope() as session:
        try:
            table_id = build_airtable_adapter(session, settings).bootstrap_table()
        except CrmError as exc:
            print(f"Could not create the table: {exc.message}")
            return 2
    print(
        f"Created table '{settings.airtable_table}' ({table_id}) "
        f"in base {settings.airtable_base_id}."
    )
    print("Run `make crm-check` to confirm every field, then set CRM_DESTINATION=airtable.")
    return 0


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


def ai_smoke(argv: list[str]) -> int:
    """Make one real OpenAI call for one demo business and print what came back. Stores nothing.

    The second of only two commands that talk to a live external API, and a human has to
    run it. It spends the client's money — set the monthly limit in the OpenAI dashboard
    first. The session is rolled back at the end: no classification row, no opportunity,
    no api_calls row and no budget counter is written.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli ai-smoke")
    parser.add_argument("--domain", help="Classify the business with this domain (default: any)")
    parser.add_argument("--escalation", action="store_true", help="Use the escalation model")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.openai_api_key.get_secret_value():
        print("OPENAI_API_KEY is not set. Put the client's key in .env first.")
        return 2
    tier: Tier = "escalation" if args.escalation else "triage"
    model = settings.ai_escalation_model if args.escalation else settings.ai_triage_model
    if not model:
        print(
            "AI_TRIAGE_MODEL / AI_ESCALATION_MODEL are not set. Copy exact model names into .env."
        )
        return 2

    from sqlalchemy import select

    from app.modules.ai import guardrails
    from app.modules.ai.budget import estimate_cost
    from app.modules.ai.client import LLMError, LLMRequest
    from app.modules.ai.openai_client import OpenAIClient
    from app.modules.ai.prompt import build_input, render
    from app.modules.ai.schema import SCHEMA_NAME, SchemaDriftError, json_schema, parse_output
    from app.modules.audit_web.models import AuditStatus, WebsiteAudit
    from app.modules.businesses.models import Business
    from app.modules.opportunities.catalogue import service_keys

    with session_scope() as session:
        stmt = (
            select(Business, WebsiteAudit)
            .join(WebsiteAudit, WebsiteAudit.business_id == Business.id)
            .where(WebsiteAudit.status == AuditStatus.done, WebsiteAudit.page_text.is_not(None))
            .order_by(WebsiteAudit.created_at.desc())
        )
        if args.domain:
            stmt = stmt.where(Business.domain == args.domain.strip().lower())
        row = session.execute(stmt.limit(1)).first()
        if row is None:
            print("No audited business with page text was found. Run `make load-demo-data` first.")
            return 2
        business, audit = row
        classification_input = build_input(business, audit, settings=settings)
        system, user = render(classification_input)
        context = guardrails.GuardrailContext.build(
            corpus=classification_input.corpus(),
            finding_codes=classification_input.finding_codes,
            urls=classification_input.urls(),
            industries=classification_input.industries,
            intent_patterns=settings.ai_explicit_intent_patterns,
            page_url=classification_input.page_url,
        )
        # No meter and no limiter: this call is deliberately not recorded anywhere.
        client = OpenAIClient(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
        )
        request = LLMRequest(
            model=model,
            tier=tier,
            system=system,
            user=user,
            schema_name=SCHEMA_NAME,
            json_schema=json_schema(service_keys()),
            fixture_key=business.domain,
        )
        print(f"Classifying {business.display_name} ({business.domain}) with {model} ...")
        try:
            result = client.complete(request)
        except LLMError as exc:
            print(f"The call failed: {exc}")
            return 1
        finally:
            client.close()
            session.rollback()

    cost = estimate_cost(settings, tier, tokens_in=result.tokens_in, tokens_out=result.tokens_out)
    print(f"Tokens: {result.tokens_in} in, {result.tokens_out} out; {result.latency_ms} ms")
    print(f"Estimated cost: {'$' + str(cost) if cost is not None else 'unknown (prices not set)'}")
    try:
        parsed = parse_output(result.text)
    except SchemaDriftError as exc:
        print(f"The answer did not match the schema ({exc.reason}). Raw text:")
        print(result.text[:4000])
        return 1
    checked = guardrails.apply(parsed, context)
    print("Validated output (after guardrails):")
    print(checked.output.model_dump_json(indent=2))
    if checked.rejected_claims:
        print("Rejected claims:")
        for claim in checked.rejected_claims:
            print(f"  - {claim}")
    print("Nothing was stored.")
    return 0


def backup_command(argv: list[str]) -> int:
    """`make backup`: one `pg_dump -Fc` into BACKUP_DIR, then keep the newest BACKUP_KEEP.

    Recorded as a `backup` job run so the health page shows it beside the scheduled ones.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli backup")
    parser.parse_args(argv)

    from app.core.backup import create_backup
    from app.modules.jobs.service import BACKUP_JOB_KIND, run_inline

    outcome: dict[str, object] = {}

    def work(session: Session, run: JobRun) -> dict[str, Any]:
        result = create_backup()
        outcome["result"] = result
        return result.summary()

    with session_scope() as session:
        run = run_inline(session, kind=BACKUP_JOB_KIND, work=work)
        status = run.status
        error = run.error

    if status is not JobRunStatus.done:
        print(f"Backup FAILED: {error}")
        return 1
    result = outcome["result"]
    assert isinstance(result, BackupResult)
    print(f"Backup written: {result.file.path} ({result.file.size_bytes} bytes)")
    if result.pruned:
        print(f"Removed {len(result.pruned)} old backup(s): {', '.join(result.pruned)}")
    print("The dump holds the database only. Copy .env.prod somewhere safe separately.")
    return 0


def restore_command(argv: list[str]) -> int:
    """`make restore FILE=…` (after its typed confirmation): restore one dump in place.

    Refuses without `--yes`, because this replaces every table. The Makefile stops the
    api and the worker first and runs the migrations afterwards.
    """
    parser = argparse.ArgumentParser(prog="python -m app.cli restore")
    parser.add_argument("--file", required=True, help="Path of the .dump inside the container")
    parser.add_argument("--yes", action="store_true", help="Confirm replacing the database")
    args = parser.parse_args(argv)

    from app.core.backup import BackupError, restore_backup

    if not args.yes:
        print("Refusing: a restore replaces the whole database. Run `make restore FILE=…`.")
        return 2
    try:
        name = restore_backup(args.file)
    except BackupError as exc:
        print(f"Restore FAILED: {exc}")
        return 1
    print(f"Restored {name}. Now run the migrations (`make migrate`) and start api + worker.")
    return 0


def backup_verify_command(argv: list[str]) -> int:
    """`make backup-verify`: restore the newest dump into a throw-away database and check it."""
    parser = argparse.ArgumentParser(prog="python -m app.cli backup-verify")
    parser.add_argument("--file", help="Verify this dump instead of the newest one")
    args = parser.parse_args(argv)

    from app.core.backup import VerifyResult, verify_backup
    from app.modules.jobs.service import BACKUP_VERIFY_JOB_KIND, run_inline

    outcome: dict[str, object] = {}

    def work(session: Session, run: JobRun) -> dict[str, Any]:
        result = verify_backup(file=args.file)
        outcome["result"] = result
        if not result.ok:
            from app.modules.alerts import service as alerts

            alerts.raise_alert(
                session,
                alerts.RULE_BACKUP_VERIFY_FAILED,
                f"Backup verify failed: {result.error}",
                severity="critical",
                details=result.summary(),
            )
            raise RuntimeError(result.error or "verify failed")
        return result.summary()

    with session_scope() as session:
        run = run_inline(session, kind=BACKUP_VERIFY_JOB_KIND, work=work)
        status = run.status

    result = outcome.get("result")
    if not isinstance(result, VerifyResult):  # pragma: no cover - verify never raises
        print("Verify FAILED before it could start.")
        return 1
    if status is not JobRunStatus.done or not result.ok:
        print(f"Backup verify FAILED for {result.file or 'no file'}: {result.error}")
        return 1
    print(f"Backup verify OK: {result.file} restored into {result.database} and dropped again.")
    print(f"  alembic_version {result.alembic_current} = head")
    print("  " + ", ".join(f"{table}={count}" for table, count in result.counts.items()))
    return 0


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "seed-admin": seed_admin,
    "seed-demo-users": seed_demo_users,
    "sync-sources": sync_sources,
    "purge-expired": purge_expired_command,
    "recompute-businesses": recompute_businesses,
    "places-smoke": places_smoke,
    "ai-smoke": ai_smoke,
    "load-demo-data": load_demo_data_command,
    "reset-demo-data": reset_demo_data_command,
    "reset-password": reset_password,
    "crm-check": crm_check,
    "crm-bootstrap-airtable": crm_bootstrap_airtable,
    "backup": backup_command,
    "restore": restore_command,
    "backup-verify": backup_verify_command,
}


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    check_startup()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print(f"usage: python -m app.cli [{' | '.join(COMMANDS)}] [options]")
        return 2
    return COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
