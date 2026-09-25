"""No API key may reach a log line through a URL.

Until v0.11.0 the PageSpeed key travelled as `&key=` in the request URL, and httpx logged
every request URL at INFO: the key sat in plain text in the worker log. Three things
failed at once, so each is pinned here separately — the key's place in the request, the
level httpx is allowed to log at, and the scrubber that is the last line of defence.
"""

import io
import logging
from collections.abc import Iterator

import fakeredis
import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.logging import QUIET_HTTP_LOGGERS, REDACTED, configure_logging, scrub
from app.modules.adapters.google_places.client import (
    PLACES_BASE_URL,
    TEXT_SEARCH_PATH,
    build_client,
)
from app.modules.audit_web.psi import PSI_ENDPOINT, build_psi_client
from tests.conftest import FakeClock

PSI_SENTINEL = "psi-SENTINEL-key-0123456789"
PLACES_SENTINEL = "places-SENTINEL-key-0123456789"


@pytest.fixture
def production_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Logging configured the way the api and worker configure it, writing to a buffer.

    The configured stdout handler is pointed at a buffer this test owns, so what it reads
    is exactly what production would have printed — and an empty buffer cannot pass for
    a clean one (each test logs a known line first).
    """
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_DIR", "")
    monkeypatch.setenv("PAGESPEED_API_KEY", PSI_SENTINEL)
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", PLACES_SENTINEL)
    get_settings.cache_clear()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    saved_levels = {name: logging.getLogger(name).level for name in QUIET_HTTP_LOGGERS}
    configure_logging()
    buffer = io.StringIO()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setStream(buffer)
    yield buffer
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    for name, level in saved_levels.items():
        logging.getLogger(name).setLevel(level)


def test_real_psi_and_places_calls_log_no_key_and_put_none_in_a_url(
    production_logging: io.StringIO, mock_http: respx.MockRouter
) -> None:
    psi_route = mock_http.get(url__startswith=PSI_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"lighthouseResult": {"categories": {}}})
    )
    places_route = mock_http.post(f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}").mock(
        return_value=httpx.Response(200, json={})
    )
    clock = FakeClock()
    redis_client = fakeredis.FakeStrictRedis()

    build_psi_client(
        settings=Settings(environment="staging", jwt_secret=SecretStr("x" * 32)),
        redis_client=redis_client,
        clock=clock,
        sleeper=clock.sleep,
        meter=lambda call: None,
    ).analyse("https://www.example-business.com/")
    build_client(
        "google_places",
        redis_client=redis_client,
        clock=clock,
        sleeper=clock.sleep,
        meter=lambda call: None,
    ).search_text("plumber in Austin, TX")

    for route, sentinel in ((psi_route, PSI_SENTINEL), (places_route, PLACES_SENTINEL)):
        assert route.call_count == 1
        request = route.calls[0].request
        assert sentinel not in str(request.url), "a key in a URL is a key in someone's log"
        assert request.headers["X-Goog-Api-Key"] == sentinel
    logging.getLogger("app.test").info("capture check")
    logged = production_logging.getvalue()
    assert "capture check" in logged, "the capture works, so an absence below means something"
    assert PSI_SENTINEL not in logged
    assert PLACES_SENTINEL not in logged


def test_httpx_may_not_log_request_urls(production_logging: io.StringIO) -> None:
    for name in QUIET_HTTP_LOGGERS:
        assert not logging.getLogger(name).isEnabledFor(logging.INFO), name


def test_a_key_in_a_logged_url_is_scrubbed_even_if_one_slips_through(
    production_logging: io.StringIO,
) -> None:
    """The last line of defence, for a future client that puts a credential in a URL."""
    logging.getLogger("httpx").warning(
        "HTTP Request: GET https://api.example.com/v1/x?url=a&key=AIzaUnknownKey123&strategy=mobile"
    )

    logged = production_logging.getvalue()
    assert "AIzaUnknownKey123" not in logged
    assert f"key={REDACTED}&strategy=mobile" in logged


@pytest.mark.parametrize(
    "url",
    [
        "https://x.test/?key=s3cr3tvalue",
        "https://x.test/?a=1&api_key=s3cr3tvalue",
        "https://x.test/?access_token=s3cr3tvalue#frag",
        "https://x.test/?apiKey=s3cr3tvalue&b=2",
    ],
)
def test_url_credentials_are_scrubbed(url: str) -> None:
    assert "s3cr3tvalue" not in scrub(url)


def test_every_secret_setting_is_scrubbed_without_being_listed_by_hand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PageSpeed key reached the log partly because a hand-kept list left it out."""
    secret_fields = [
        name
        for name, field in Settings.model_fields.items()
        if field.annotation is SecretStr and name != "jwt_secret"
    ]
    assert "pagespeed_api_key" in secret_fields
    for name in secret_fields:
        monkeypatch.setenv(name.upper(), f"literal-{name}-value")
    get_settings.cache_clear()

    for name in secret_fields:
        value = f"literal-{name}-value"
        assert value not in scrub(f"something mentioned {value} in passing"), name
