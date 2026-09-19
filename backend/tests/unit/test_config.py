"""Settings come from the environment and parse the way .env.example writes them."""

import pytest

from app.core.config import Settings


def test_cors_origins_accepts_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test ,")
    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_has_a_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings().cors_origins == ["http://localhost:3000"]


def test_the_jwt_secret_is_not_printed_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "a-very-secret-value")
    settings = Settings()
    assert "a-very-secret-value" not in repr(settings)
    assert settings.jwt_secret.get_secret_value() == "a-very-secret-value"


def test_booleans_parse_from_the_env_file_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_COOKIE_SECURE", "false")
    monkeypatch.setenv("JOB_QUEUE_IS_ASYNC", "true")
    settings = Settings()
    assert settings.refresh_cookie_secure is False
    assert settings.job_queue_is_async is True


def test_blank_ai_settings_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` leaves the provider and the prices blank; blank is not a value."""
    for name in (
        "AI_PROVIDER",
        "AI_TRIAGE_PRICE_IN_PER_M",
        "AI_TRIAGE_PRICE_OUT_PER_M",
        "AI_ESCALATION_PRICE_IN_PER_M",
        "AI_ESCALATION_PRICE_OUT_PER_M",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ENVIRONMENT", "ci")

    settings = Settings()

    assert settings.ai_provider is None
    assert settings.ai_triage_price_in_per_m is None
    assert settings.resolved_ai_provider == "fake"


def test_the_ai_provider_resolves_from_the_key_and_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings().resolved_ai_provider == "disabled"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-something")
    assert Settings().resolved_ai_provider == "openai"

    monkeypatch.setenv("AI_PROVIDER", "fake")
    assert Settings().resolved_ai_provider == "fake", "an explicit choice always wins"

    monkeypatch.setenv("AI_EXPLICIT_INTENT_PATTERNS", "need a site, hiring a designer")
    assert Settings().ai_explicit_intent_patterns == ["need a site", "hiring a designer"]
