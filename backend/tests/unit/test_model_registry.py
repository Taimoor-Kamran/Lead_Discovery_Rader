"""Every entrypoint must see the whole schema.

The worker process imports `app.workers.tasks` and nothing else. If that import does not
pull in every model, SQLAlchemy cannot resolve `audit_logs.actor_id -> users.id` and the
first job run dies at flush time. These tests import each entrypoint in a clean
interpreter and check the metadata is complete.
"""

import subprocess
import sys

import pytest

from app.models_registry import Base

EXPECTED = {
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
}

CHECK = (
    "import {module};"
    "from app.core.db import Base;"
    "missing = {expected} - set(Base.metadata.tables);"
    "print(sorted(missing))"
)


@pytest.mark.parametrize("module", ["app.workers.tasks", "app.workers.main", "app.main", "app.cli"])
def test_entrypoint_sees_every_table(module: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", CHECK.format(module=module, expected=EXPECTED)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", f"{module} is missing tables: {result.stdout}"


def test_the_registry_lists_every_table() -> None:
    assert set(Base.metadata.tables) >= EXPECTED
