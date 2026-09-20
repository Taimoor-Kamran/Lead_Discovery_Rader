"""No key-shaped string may be committed (spec v0.8.0 §5).

Greps every tracked file except `.env*` and the recorded fixtures for the shapes the
three providers' secrets take. A hit is a failing test, so a pasted key never survives
`make check`, let alone a push.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent

# A line carrying this marker is a deliberate, documented look-alike (a redaction test's
# sentinel). Real keys never get one, because a reviewer would ask why.
ALLOW_MARKER = "secrets-hygiene: allow"

KEY_SHAPES = {
    # OpenAI: sk-… (and the newer sk-proj-…); at least 20 more characters.
    "openai": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),
    # Google API keys: AIza followed by 35 URL-safe characters.
    "google": re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    # Airtable personal access tokens: pat + 14 chars, a dot, 64 hex characters.
    "airtable": re.compile(r"\bpat[A-Za-z0-9]{14}\.[a-f0-9]{64}\b"),
}
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    ".next",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".lock", ".dump"}


def tracked_files() -> list[Path]:
    """`git ls-files` when the repo is available; a directory walk otherwise (CI images)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],  # noqa: S607
            cwd=REPO_DIR,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
        paths = [REPO_DIR / p.decode() for p in out.split(b"\0") if p]
    except (OSError, subprocess.SubprocessError):
        paths = []
        for root, dirs, files in os.walk(REPO_DIR):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            paths.extend(Path(root) / f for f in files)
    return [p for p in paths if p.is_file()]


def is_excluded(path: Path) -> bool:
    relative = path.relative_to(REPO_DIR)
    if relative.name.startswith(".env"):
        return True  # .env, .env.example, .env.prod.example: the operator's own files
    if "fixtures" in relative.parts:
        return True  # recorded API responses carry redacted placeholders of their own
    if relative.suffix.lower() in SKIP_SUFFIXES:
        return True
    return any(part in SKIP_DIRS for part in relative.parts)


def test_no_key_shaped_string_is_committed() -> None:
    hits: list[str] = []
    files = [p for p in tracked_files() if not is_excluded(p)]
    assert len(files) > 50, "the walk found the repository"
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line_text in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line_text:
                continue
            for provider, pattern in KEY_SHAPES.items():
                if pattern.search(line_text):
                    hits.append(
                        f"{path.relative_to(REPO_DIR)}:{number}: looks like a {provider} key"
                    )
    assert hits == [], "\n".join(hits)


@pytest.mark.parametrize(
    ("provider", "sample"),
    [
        ("openai", "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"),
        ("openai", "sk-proj-" + "Zz9" * 10),
        ("google", "AIza" + "0" * 35),
        ("airtable", "pat" + "A" * 14 + "." + "f" * 64),
    ],
)
def test_the_shapes_catch_real_looking_keys(provider: str, sample: str) -> None:
    assert KEY_SHAPES[provider].search(f"key = '{sample}'")
