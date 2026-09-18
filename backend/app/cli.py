"""Small operational commands. Run with `python -m app.cli <command>`."""

import os
import secrets
import sys

from app import models_registry  # noqa: F401
from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.modules.auth.service import ensure_admin

logger = get_logger("app.cli")

GENERATED_PASSWORD_BYTES = 18


def seed_admin() -> int:
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


COMMANDS = {"seed-admin": seed_admin}


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print(f"usage: python -m app.cli [{' | '.join(COMMANDS)}]")
        return 2
    return COMMANDS[args[0]]()


if __name__ == "__main__":
    raise SystemExit(main())
