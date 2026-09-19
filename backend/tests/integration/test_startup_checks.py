"""Every entrypoint refuses to start with a JWT secret that is too weak to trust.

An API that signs tokens with a guessable secret is worse than an API that does not come
up at all, so this is a startup failure rather than a warning — outside development, where
a warning is what a developer needs.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.security import InsecureConfigurationError

SHORT_SECRET = "0123456789abcdef"  # 16 bytes: half of what HS256 asks for
STRONG_SECRET = "0123456789abcdef0123456789abcdef"  # exactly 32


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


def test_the_api_refuses_to_start_in_production_with_a_short_secret(
    environment: None,
) -> None:
    set_env(ENVIRONMENT="production", APP_ENV="production", JWT_SECRET=SHORT_SECRET)

    from app.main import create_app

    with pytest.raises(InsecureConfigurationError) as exc:
        create_app()

    message = str(exc.value)
    assert "16 bytes" in message
    assert "openssl rand -hex 32" in message
    assert SHORT_SECRET not in message, "the secret itself never reaches the message"


def test_the_api_starts_in_production_with_a_strong_secret(environment: None) -> None:
    set_env(ENVIRONMENT="production", APP_ENV="production", JWT_SECRET=STRONG_SECRET)

    from app.main import create_app

    assert create_app() is not None


def test_the_api_only_warns_in_development(
    environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """`configure_logging` owns the root handlers, so the warning is read off stdout."""
    set_env(ENVIRONMENT="development", APP_ENV="development", JWT_SECRET=SHORT_SECRET)

    from app.main import create_app

    assert create_app() is not None

    printed = capsys.readouterr().out
    assert "shorter than the recommended minimum" in printed
    assert SHORT_SECRET not in printed


@pytest.mark.parametrize("module", ["app.cli", "app.workers.main"])
def test_the_other_entrypoints_check_the_secret_too(module: str, environment: Any) -> None:
    """Importing is not enough to trigger it — the check sits in each `main()`."""
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(module))

    assert "check_jwt_secret" in source


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
    result = run_cli(
        ["sync-sources"],
        {"ENVIRONMENT": "production", "APP_ENV": "production", "JWT_SECRET": SHORT_SECRET},
    )

    assert result.returncode != 0
    assert "InsecureConfigurationError" in result.stderr or "openssl rand" in result.stderr
