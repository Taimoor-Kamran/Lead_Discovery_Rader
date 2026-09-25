"""`make reset-password` must hand NEW_PASSWORD into the container.

The main README documents `NEW_PASSWORD='...' make reset-password EMAIL=...`. Until v0.11.2
the recipe's `docker compose run` did not pass the variable on, so the command inside the
container never saw it. This expands the real recipe with `make -n` (nothing is run) and
checks the `-e NEW_PASSWORD` is there, in development and production mode, and that the
password itself never lands on the command line.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parents[3]
SENTINEL = "sentinel-passphrase-never-on-a-command-line"


def dry_run(*extra: str) -> str:
    env = {**os.environ, "NEW_PASSWORD": SENTINEL}
    result = subprocess.run(  # noqa: S603 - fixed argv, no user input
        [  # noqa: S607
            "make",
            "-n",
            "--no-print-directory",
            "reset-password",
            "EMAIL=someone@example.com",
            *extra,
        ],
        cwd=REPO_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout


@pytest.mark.parametrize("extra", [(), ("PROD=1",)], ids=["development", "production"])
def test_the_recipe_passes_new_password_into_the_container(extra: tuple[str, ...]) -> None:
    out = dry_run(*extra)
    [command] = [line for line in out.splitlines() if "app.cli reset-password" in line]

    assert " run --rm -e NEW_PASSWORD api " in command
    assert SENTINEL not in out, "only the name is passed; the value never appears"
