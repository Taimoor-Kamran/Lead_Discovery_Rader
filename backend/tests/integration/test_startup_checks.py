"""Every entrypoint refuses to start with a configuration that is unsafe for production.

An API that signs tokens with a guessable secret, or that can answer from demo fixtures,
is worse than an API that does not come up at all, so under `staging` and `production`
each rule below is a startup failure rather than a warning. Under development the JWT rule
warns and the rest do not apply. One test per rule, as the spec asks.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from pydantic import ValidationError

from app.cli import main as cli_main
from app.core.config import Settings, get_settings
from app.core.security import InsecureConfigurationError
from app.core.startup import check_startup, production_problems

# Imported here, under the test environment, because `app.main` builds the application at
# import time: importing it inside a production-mode test would raise at the wrong place.
from app.main import create_app

SHORT_SECRET = "0123456789abcdef"  # 16 bytes: half of what HS256 asks for
STRONG_SECRET = "0123456789abcdef0123456789abcdef"  # exactly 32

# A configuration that passes every production rule. Each test breaks exactly one thing.
SAFE_PRODUCTION_ENV = {
    "ENVIRONMENT": "production",
    "APP_ENV": "production",
    "JWT_SECRET": STRONG_SECRET,
    "DEBUG": "false",
    "ADMIN_EMAIL": "ops@agency.test",
    "DEMO_USERS_PASSWORD": "",
    "CORS_ORIGINS": "http://127.0.0.1:3000",
    "AI_PROVIDER": "disabled",
    "CRM_DESTINATION": "csv",
}


@pytest.fixture
def environment() -> Iterator[None]:
    """Restore the process environment and the settings cache after each case."""
    previous = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)
        get_settings.cache_clear()


def set_env(**values: str) -> None:
    os.environ.update(values)
    get_settings.cache_clear()


def production(**overrides: str) -> None:
    set_env(**{**SAFE_PRODUCTION_ENV, **overrides})


# --- the JWT rule, in both directions ------------------------------------------------


def test_the_api_refuses_to_start_in_production_with_a_short_secret(
    environment: None,
) -> None:
    production(JWT_SECRET=SHORT_SECRET)

    with pytest.raises(InsecureConfigurationError) as exc:
        create_app()

    message = str(exc.value)
    assert "16 bytes" in message
    assert "openssl rand -hex 32" in message
    assert SHORT_SECRET not in message, "the secret itself never reaches the message"


def test_the_api_starts_in_production_with_a_safe_configuration(environment: None) -> None:
    production()

    assert create_app() is not None


def test_the_api_only_warns_in_development(
    environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """`configure_logging` owns the root handlers, so the warning is read off stdout."""
    set_env(ENVIRONMENT="development", APP_ENV="development", JWT_SECRET=SHORT_SECRET)

    assert create_app() is not None

    printed = capsys.readouterr().out
    assert "shorter than the recommended minimum" in printed
    assert SHORT_SECRET not in printed


# --- one test per production rule ------------------------------------------------------


def test_debug_is_refused_in_production(environment: None) -> None:
    production(DEBUG="true")
    with pytest.raises(InsecureConfigurationError, match="DEBUG=true"):
        check_startup()


def test_demo_fixtures_are_refused_in_production(environment: None) -> None:
    """`fixtures_allowed` is derived from the environment and is false in production; the
    rule still guards it, so a future override cannot slip fixtures into production."""
    production()
    settings = get_settings()
    assert settings.fixtures_allowed is False
    assert production_problems(settings) == []

    with patch.object(Settings, "fixtures_allowed", new_callable=PropertyMock, return_value=True):
        problems = production_problems(settings)
    assert any("demo fixtures" in problem for problem in problems)

    from app.modules.adapters import registry

    assert "demo_fixture" not in registry.names(), "the demo adapter is not registered"


def test_the_fake_crm_destination_is_refused_in_production(environment: None) -> None:
    production(CRM_DESTINATION="fake")
    # The settings model itself refuses, before any entrypoint runs.
    with pytest.raises(ValidationError, match="CRM_DESTINATION=fake"):
        Settings()


def test_the_fake_ai_provider_is_refused_in_production(environment: None) -> None:
    production(AI_PROVIDER="fake")
    with pytest.raises(InsecureConfigurationError, match="AI_PROVIDER=fake"):
        check_startup()


@pytest.mark.parametrize("email", ["admin@example.com", "Admin@Example.com", "ops@example.com"])
def test_a_placeholder_admin_email_is_refused_in_production(environment: None, email: str) -> None:
    production(ADMIN_EMAIL=email)
    with pytest.raises(InsecureConfigurationError, match="ADMIN_EMAIL is a placeholder"):
        check_startup()


def test_a_demo_users_password_is_refused_in_production(environment: None) -> None:
    production(DEMO_USERS_PASSWORD="demo-users-password-value")
    with pytest.raises(InsecureConfigurationError, match="DEMO_USERS_PASSWORD") as exc:
        check_startup()
    assert "demo-users-password-value" not in str(exc.value)


def test_a_wildcard_cors_origin_is_refused_in_production(environment: None) -> None:
    production(CORS_ORIGINS="http://127.0.0.1:3000, *")
    with pytest.raises(InsecureConfigurationError, match="CORS_ORIGINS"):
        check_startup()


def test_every_problem_is_listed_at_once(environment: None) -> None:
    production(DEBUG="true", ADMIN_EMAIL="admin@example.com", CORS_ORIGINS="*")
    with pytest.raises(InsecureConfigurationError) as exc:
        check_startup()
    message = str(exc.value)
    assert "DEBUG=true" in message
    assert "ADMIN_EMAIL" in message
    assert "CORS_ORIGINS" in message


def test_the_refresh_cookie_is_secure_in_production_whatever_the_setting(
    environment: None,
) -> None:
    production(REFRESH_COOKIE_SECURE="false")
    assert get_settings().resolved_refresh_cookie_secure is True
    set_env(ENVIRONMENT="ci", APP_ENV="ci", REFRESH_COOKIE_SECURE="false")
    assert get_settings().resolved_refresh_cookie_secure is False


# --- every entrypoint runs the check -----------------------------------------------


@pytest.mark.parametrize("module", ["app.cli", "app.workers.main", "app.main"])
def test_the_other_entrypoints_check_the_configuration_too(module: str, environment: Any) -> None:
    """Importing is not enough to trigger it — the check sits in each `main()`."""
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(module))

    assert "check_startup" in source


def run_cli(argv: list[str], extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the CLI in its own process, the way an operator would."""
    env = {**os.environ, **extra_env}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli", *argv],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )


def test_the_cli_refuses_to_run_a_command_in_production_with_a_short_secret(
    environment: None,
) -> None:
    result = run_cli(["sync-sources"], {**SAFE_PRODUCTION_ENV, "JWT_SECRET": SHORT_SECRET})

    assert result.returncode != 0
    assert "InsecureConfigurationError" in result.stderr or "openssl rand" in result.stderr


# --- demo commands refuse in production, uniformly -----------------------------------


@pytest.mark.parametrize(
    "command",
    [["seed-demo-users"], ["load-demo-data"], ["reset-demo-data"]],
)
def test_demo_commands_refuse_in_production(
    environment: None, command: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Refused before anything touches the database; the message names the command."""
    production()

    assert cli_main(command) == 2

    printed = capsys.readouterr().out
    assert "APP_ENV is 'production'" in printed
    assert command[0] in printed
    assert "refused" in printed


@pytest.mark.parametrize(
    "command",
    [["seed-demo-users"], ["load-demo-data"], ["reset-demo-data"]],
)
def test_demo_commands_refuse_in_staging_too(
    environment: None, command: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    set_env(**{**SAFE_PRODUCTION_ENV, "ENVIRONMENT": "staging", "APP_ENV": "staging"})

    assert cli_main(command) == 2
    assert "refused" in capsys.readouterr().out
