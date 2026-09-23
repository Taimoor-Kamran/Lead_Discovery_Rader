"""What every entrypoint checks before it does anything else.

Production mode on one machine is only safe when nothing demo-shaped can run and nothing
guessable signs tokens. `check_startup` is called from the API factory, the worker and the
CLI, so there is no way into the system that skips it. Under `local`, `development` and
`ci` the JWT check warns and everything else is allowed; under `staging` and `production`
every rule below is a refusal to start, listed all at once so an operator fixes `.env.prod`
in one pass rather than one restart at a time.
"""

from app.core.config import MIN_JWT_SECRET_BYTES, Settings, get_settings
from app.core.security import InsecureConfigurationError, check_jwt_secret

PLACEHOLDER_DOMAIN = "@example.com"

# The AI providers production accepts. `disabled` is **deliberately** on this list, not an
# oversight to tidy away: rules-only is a supported configuration and the MVP's default
# (spec v0.10.0 §3), so the deterministic rules run and no LLM is called at all. `fake`
# answers from checked-in files, which is why it is the one refused here.
PRODUCTION_AI_PROVIDERS = frozenset({"openai", "disabled"})


def production_problems(settings: Settings) -> list[str]:
    """Every reason this configuration may not run in production. Empty means it may."""
    problems: list[str] = []
    if not settings.jwt_secret_is_strong:
        actual = len(settings.jwt_secret.get_secret_value().encode())
        problems.append(
            f"JWT_SECRET is {actual} bytes; HS256 needs at least {MIN_JWT_SECRET_BYTES} "
            "(generate one with: openssl rand -hex 32)"
        )
    if settings.debug:
        problems.append("DEBUG=true is not allowed in production")
    if settings.fixtures_allowed or settings.is_development:
        problems.append("demo fixtures are enabled; they exist only under local/development/ci")
    if settings.crm_destination == "fake":
        problems.append("CRM_DESTINATION=fake is an in-database stand-in, not a destination")
    # `resolved_ai_provider` is what the pipeline will actually talk to: an explicit
    # AI_PROVIDER, or what the key and the environment resolve to. Checking the resolution
    # rather than the raw setting means an unset AI_PROVIDER cannot smuggle `fake` in.
    if settings.resolved_ai_provider not in PRODUCTION_AI_PROVIDERS:
        problems.append(
            f"AI_PROVIDER={settings.resolved_ai_provider} answers from checked-in files; "
            "use openai (a real key) or disabled (rules only)"
        )
    email = settings.admin_email.strip().lower()
    if email == "admin@example.com" or email.endswith(PLACEHOLDER_DOMAIN):
        problems.append(
            "ADMIN_EMAIL is a placeholder (admin@example.com or anything @example.com); "
            "set the real administrator's address"
        )
    if settings.demo_users_password.get_secret_value().strip():
        problems.append("DEMO_USERS_PASSWORD is set; demo users must not exist in production")
    if any(origin.strip() == "*" for origin in settings.cors_origins):
        problems.append("CORS_ORIGINS allows '*'; list the exact web origin instead")
    return problems


def check_startup(settings: Settings | None = None) -> None:
    """Refuse to start on an unsafe configuration, or warn where a warning is enough.

    Raises `InsecureConfigurationError` with every problem in the message. No secret
    value ever reaches the message.
    """
    config = settings or get_settings()
    if config.environment in {"staging", "production"}:
        problems = production_problems(config)
        if problems:
            listed = "\n".join(f"  - {problem}" for problem in problems)
            raise InsecureConfigurationError(
                f"Refusing to start in environment '{config.environment}':\n{listed}"
            )
        return
    check_jwt_secret(config)


def refuse_outside_development(settings: Settings, what: str) -> str | None:
    """The message a demo command prints when it must not run here, or `None` when it may.

    Shared by `seed-demo-users`, `load-demo-data` and `reset-demo-data` so the three refuse
    the same way. Production never sees fictional data or example.com users.
    """
    if settings.is_development:
        return None
    return (
        f"APP_ENV is '{settings.environment}'. {what} is for local development only and "
        "is refused here."
    )
