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
