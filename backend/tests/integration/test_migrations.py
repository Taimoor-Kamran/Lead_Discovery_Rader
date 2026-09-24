"""Alembic must apply to an empty database and come back off it cleanly."""

import uuid

from alembic import command
from sqlalchemy import Engine, create_engine, inspect, text

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
    "alerts",
}
HARDENING_TABLES = {"alerts"}
HARDENING_USER_COLUMNS = {
    "must_change_password",
    "failed_login_count",
    "locked_until",
    "last_login_at",
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


DEEPER_AUDIT_COLUMNS = {
    "website_audits": {"accessibility_score", "best_practices_score"},
    "businesses": {"rating", "user_rating_count"},
    "job_runs": {"updated_at"},  # 0010_job_run_heartbeat
}


def _columns(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_down_to_v0_8_removes_only_the_v0_11_columns_and_comes_back(database_url: str) -> None:
    """Down to `0008_hardening` undoes exactly v0.11.0's two migrations: five columns.

    Named by revision, not `-1`: v0.11.0 gained a second migration, and a count silently
    changed what this test checked.
    """
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    tables_at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "0008_hardening")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) == tables_at_head
    for table, columns in DEEPER_AUDIT_COLUMNS.items():
        assert columns & _columns(engine, table) == set(), table
    assert "must_change_password" in _columns(engine, "users"), "only v0.11.0 comes off"
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(url)
    for table, columns in DEEPER_AUDIT_COLUMNS.items():
        assert columns <= _columns(engine, table), table
    engine.dispose()


def test_down_to_v0_7_and_back_up_leaves_the_schema_as_it_was(database_url: str) -> None:
    """Down to `0007_crm` must undo v0.8.0 (and anything after it) and nothing else.

    The steps are named by revision, not counted: a count silently changes meaning each
    time a migration is added on top.
    """
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "0007_crm")
    engine = create_engine(url)
    after_downgrade = set(inspect(engine).get_table_names())
    user_columns = {c["name"] for c in inspect(engine).get_columns("users")}
    job_columns = {c["name"] for c in inspect(engine).get_columns("search_jobs")}
    engine.dispose()

    assert at_head - after_downgrade == HARDENING_TABLES
    assert HARDENING_USER_COLUMNS & user_columns == set()
    assert "max_results" not in job_columns
    assert after_downgrade >= CRM_TABLES, "only v0.8.0 comes off"

    command.upgrade(config, "head")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) == at_head
    assert {c["name"] for c in inspect(engine).get_columns("users")} >= HARDENING_USER_COLUMNS
    assert "max_results" in {c["name"] for c in inspect(engine).get_columns("search_jobs")}
    engine.dispose()


def test_down_to_v0_6_takes_the_crm_with_it(database_url: str) -> None:
    """Down to `0006_review` removes v0.8.0 and the v0.7.0 CRM tables, nothing else."""
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "0006_review")
    engine = create_engine(url)
    after_downgrade = set(inspect(engine).get_table_names())
    enums_after = _enums(engine)
    engine.dispose()

    assert at_head - after_downgrade == CRM_TABLES | HARDENING_TABLES
    assert CRM_ENUMS & enums_after == set()
    assert {"review_decisions", "suppressions"} <= after_downgrade, "only v0.7.0+ comes off"
    assert {"review_decision", "suppression_source", "review_status"} <= enums_after

    command.upgrade(config, "head")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) == at_head
    assert _enums(engine) >= CRM_ENUMS
    engine.dispose()


def test_down_to_v0_5_takes_review_with_it(database_url: str) -> None:
    """Down to `0005_opportunities` removes the CRM and review tables and review columns."""
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "0005_opportunities")
    engine = create_engine(url)
    after_downgrade = set(inspect(engine).get_table_names())
    enums_after = _enums(engine)
    opportunity_columns = {c["name"] for c in inspect(engine).get_columns("opportunities")}
    engine.dispose()

    assert at_head - after_downgrade == CRM_TABLES | HARDENING_TABLES | {
        "review_decisions",
        "suppressions",
    }
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


def test_down_to_v0_2_takes_entity_resolution_with_it(database_url: str) -> None:
    """The v0.3.0 migration owns the businesses tables and the columns it added."""
    url = _fresh_database(database_url)
    config = alembic_config(url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    at_head = set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "0002_discovery")
    engine = create_engine(url)
    after_downgrade = set(inspect(engine).get_table_names())
    record_columns = {c["name"] for c in inspect(engine).get_columns("discovered_records")}
    user_columns = {c["name"] for c in inspect(engine).get_columns("users")}
    engine.dispose()

    assert at_head - after_downgrade == CRM_TABLES | HARDENING_TABLES | {
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
