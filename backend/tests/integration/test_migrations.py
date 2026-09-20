"""Alembic must apply to an empty database and come back off it cleanly."""

import uuid

from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.conftest import alembic_config

EXPECTED_TABLES = {
    "users",
    "sources",
    "search_jobs",
    "job_runs",
    "audit_logs",
    "discovered_records",
    "record_sightings",
    "api_calls",
    "businesses",
    "business_field_values",
    "match_candidates",
    "website_audits",
    "ai_classifications",
    "opportunities",
    "review_decisions",
    "suppressions",
    "crm_leads",
    "crm_lead_opportunities",
    "crm_sync_attempts",
    "crm_fake_records",
}
CRM_TABLES = {"crm_leads", "crm_lead_opportunities", "crm_sync_attempts", "crm_fake_records"}
CRM_ENUMS = {"crm_lead_status", "crm_sync_action", "crm_sync_status"}
EXPECTED_ENUMS = {
    "user_role",
    "source_kind",
    "search_job_status",
    "job_run_status",
    "website_kind",
    "business_status",
    "match_candidate_status",
    "resolution_status",
    "website_audit_status",
    "ai_classification_status",
    "opportunity_source",
    "review_status",
    "review_decision",
    "suppression_source",
    "crm_lead_status",
    "crm_sync_action",
    "crm_sync_status",
}


def _fresh_database(database_url: str) -> str:
    """Create an empty database next to the one the other tests use."""
    name = f"migration_check_{uuid.uuid4().hex[:8]}"
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    base, _, _ = database_url.rpartition("/")
    return f"{base}/{name}"


def _enums(engine: object) -> set[str]:
    with engine.connect() as connection:  # type: ignore[attr-defined]
        return {
            row[0]
            for row in connection.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
        }


def test_one_step_down_and_back_up_leaves_the_schema_as_it_was(database_url: str) -> None:
    """`downgrade -1` must undo exactly the newest migration (v0.7.0 CRM) and nothing else."""
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "-1")
    engine = create_engine(url)
    after_downgrade = set(inspect(engine).get_table_names())
    enums_after = _enums(engine)
    engine.dispose()

    assert at_head - after_downgrade == CRM_TABLES
    assert CRM_ENUMS & enums_after == set()
    assert {"review_decisions", "suppressions"} <= after_downgrade, "only v0.7.0 comes off"
    assert {"review_decision", "suppression_source", "review_status"} <= enums_after

    command.upgrade(config, "head")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) == at_head
    assert _enums(engine) >= CRM_ENUMS
    engine.dispose()


def test_two_steps_down_takes_review_with_it(database_url: str) -> None:
    """`downgrade -2` removes the CRM and the review tables and the review columns."""
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "-2")
    engine = create_engine(url)
    after_downgrade = set(inspect(engine).get_table_names())
    enums_after = _enums(engine)
    opportunity_columns = {c["name"] for c in inspect(engine).get_columns("opportunities")}
    engine.dispose()

    assert at_head - after_downgrade == CRM_TABLES | {"review_decisions", "suppressions"}
    assert ({"review_decision", "suppression_source"} | CRM_ENUMS) & enums_after == set()
    assert "review_status" in enums_after, "the status enum belongs to v0.5.0"
    assert {"decided_at", "decided_by", "lock_version"} & opportunity_columns == set()
    assert "opportunities" in after_downgrade, "only v0.6.0 and v0.7.0 come off"

    command.upgrade(config, "head")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) == at_head
    assert {"decided_at", "decided_by", "lock_version"} <= {
        c["name"] for c in inspect(engine).get_columns("opportunities")
    }
    engine.dispose()


def test_five_steps_down_takes_entity_resolution_with_it(database_url: str) -> None:
    """The v0.3.0 migration owns the businesses tables and the columns it added."""
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "-5")
    engine = create_engine(url)
    after_downgrade = set(inspect(engine).get_table_names())
    record_columns = {c["name"] for c in inspect(engine).get_columns("discovered_records")}
    user_columns = {c["name"] for c in inspect(engine).get_columns("users")}
    engine.dispose()

    assert at_head - after_downgrade == CRM_TABLES | {
        "review_decisions",
        "suppressions",
        "opportunities",
        "ai_classifications",
        "website_audits",
        "businesses",
        "business_field_values",
        "match_candidates",
    }
    assert "resolution_status" not in record_columns
    assert "token_version" not in user_columns
    assert "business_id" in record_columns, "the column predates v0.3.0; only its FK is new"

    command.upgrade(config, "head")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) == at_head
    assert "resolution_status" in {
        c["name"] for c in inspect(engine).get_columns("discovered_records")
    }
    assert "token_version" in {c["name"] for c in inspect(engine).get_columns("users")}
    engine.dispose()


def test_upgrade_head_then_downgrade_base_on_an_empty_database(database_url: str) -> None:
    url = _fresh_database(database_url)
    config = alembic_config(url)

    command.upgrade(config, "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert tables >= EXPECTED_TABLES
    assert "alembic_version" in tables

    with engine.connect() as connection:
        enums = {
            row[0]
            for row in connection.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
        }
    assert enums >= EXPECTED_ENUMS
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(url)
    remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert remaining == set()
    with engine.connect() as connection:
        enums_after = {
            row[0]
            for row in connection.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
        }
    assert EXPECTED_ENUMS & enums_after == set()
    engine.dispose()
