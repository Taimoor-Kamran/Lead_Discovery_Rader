"""The SSRF guard, the caps and the robots policy.

Every rule in the spec's SafeFetcher table has a test here. Nothing in this file opens a
socket: the resolver and the backend are both injected, which is also how a "hostname
that resolves to a private address" can be tested without owning such a hostname.
"""

import inspect
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

import fakeredis
import pytest

from app.core import fetch_backends, safe_fetch
from app.core.config import Settings
from app.core.fetch_backends import (
    BackendResponse,
    ConnectFailedError,
    FetchRequest,
    FetchTimeoutError,
    TlsVerificationError,
)
from app.core.safe_fetch import (
    HostThrottle,
    SafeFetcher,
    UnsafeUrlError,
    blocked_reason,
    robots_allows,
    split_safe_url,
)
from tests.conftest import FakeClock

PUBLIC_IP = "93.184.216.34"
HOME = "https://example.test/"


@dataclass
class RecordingBackend:
    """A backend that answers from a script and remembers what it was asked."""

    responses: dict[str, BackendResponse | Exception] = field(default_factory=dict)
    default: BackendResponse | Exception | None = None
    resolves_dns: bool = True
    calls: list[str] = field(default_factory=list)
    requests: list[FetchRequest] = field(default_factory=list)

    def handles(self, host: str) -> bool:
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        self.calls.append(request.url)
        self.requests.append(request)
        answer = self.responses.get(request.url, self.default)
        if answer is None:
            return BackendResponse(status_code=404, headers={"content-type": "text/plain"})
        if isinstance(answer, Exception):
            raise answer
        return answer


def html(body: str, *, status: int = 200, truncated: bool = False) -> BackendResponse:
    return BackendResponse(
        status_code=status,
        headers={"content-type": "text/html; charset=utf-8"},
        body=body.encode(),
        truncated=truncated,
    )


def robots(body: str, *, status: int = 200) -> BackendResponse:
    return BackendResponse(
        status_code=status, headers={"content-type": "text/plain"}, body=body.encode()
    )


def build(
    backend: RecordingBackend,
    *,
    resolves_to: Sequence[str] = (PUBLIC_IP,),
    redis_client: fakeredis.FakeStrictRedis | None = None,
    clock: FakeClock | None = None,
    **settings_overrides: object,
) -> SafeFetcher:
    """A fetcher with no network, no real clock and no shared Redis."""
    ticker = clock or FakeClock()
    overrides: dict[str, object] = {
        "jwt_secret": "x" * 32,
        "bot_contact": "https://agency.example/bot",
        "audit_host_throttle_seconds": 0.0,
        **settings_overrides,
    }
    return SafeFetcher(
        redis=redis_client or fakeredis.FakeStrictRedis(),
        backends=[backend],
        settings=Settings(**overrides),  # type: ignore[arg-type]
        resolver=lambda host, port: list(resolves_to),
        clock=ticker,
        sleeper=ticker.sleep,
    )


# --- scheme, port and address rules ---------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://169.254.169.254",
        "http://[::1]",
        "http://100.64.0.1",
        "http://192.168.1.1/admin",
        "http://172.16.0.9",
        "http://0.0.0.0",  # the unspecified address must be refused
        "http://[::ffff:127.0.0.1]",
        "http://224.0.0.1",
        "http://example.test:8080",
        "ftp://example.test/",
        "file:///etc/passwd",
        "gopher://example.test/",
        "http://user:pass@example.test/",
    ],
)
def test_the_guard_refuses_an_unsafe_url(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        split_safe_url(url)


@pytest.mark.parametrize(
    "url", ["http://example.test/", "https://example.test:443/x", "http://example.test:80/"]
)
def test_the_guard_allows_a_public_http_url(url: str) -> None:
    assert split_safe_url(url).hostname == "example.test"


def test_a_hostname_resolving_to_a_private_address_is_refused() -> None:
    backend = RecordingBackend(default=html("<html></html>"))
    fetcher = build(backend, resolves_to=["10.1.2.3"])

    with pytest.raises(UnsafeUrlError) as exc:
        fetcher.fetch("https://sneaky.test/")

    assert "10.1.2.3" in exc.value.reason
    assert backend.calls == [], "nothing may be requested once an address is refused"


def test_one_private_answer_among_public_ones_still_refuses() -> None:
    fetcher = build(RecordingBackend(), resolves_to=[PUBLIC_IP, "127.0.0.1"])

    with pytest.raises(UnsafeUrlError):
        fetcher.fetch("https://mixed.test/")


def test_a_name_that_does_not_resolve_is_a_result_not_a_refusal() -> None:
    def failing_resolver(host: str, port: int) -> Sequence[str]:
        raise ConnectFailedError("DNS lookup failed")

    fetcher = build(RecordingBackend())
    fetcher.resolver = failing_resolver

    outcome = fetcher.fetch("https://nowhere.test/")

    assert outcome.reachable is False
    assert outcome.error_kind == "connect"


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1", "a loopback address"),
        ("169.254.169.254", "a link-local address"),
        ("100.64.0.1", "a CGNAT address"),
        ("10.0.0.1", "a private address"),
        ("240.0.0.1", "a reserved address"),
        ("::1", "a loopback address"),
        ("8.8.8.8", None),
        ("93.184.216.34", None),
    ],
)
def test_blocked_reason_names_the_rule(address: str, expected: str | None) -> None:
    import ipaddress

    reason = blocked_reason(ipaddress.ip_address(address))
    if expected is None:
        assert reason is None
    else:
        assert reason == expected


# --- redirects ------------------------------------------------------------------------


def redirect(to: str, *, status: int = 301) -> BackendResponse:
    return BackendResponse(status_code=status, headers={"location": to})


def test_a_redirect_chain_is_followed_and_recorded() -> None:
    backend = RecordingBackend(
        responses={
            "http://example.test/": redirect("https://example.test/"),
            "https://example.test/": html("<html><title>Home</title></html>"),
        }
    )
    fetcher = build(backend)

    outcome = fetcher.fetch("http://example.test/")

    assert outcome.final_url == "https://example.test/"
    assert outcome.redirect_chain == ("http://example.test/",)
    assert outcome.status_code == 200


def test_a_redirect_to_a_private_address_is_refused() -> None:
    backend = RecordingBackend(
        responses={HOME: redirect("http://169.254.169.254/latest/meta-data/")}
    )
    fetcher = build(backend)

    with pytest.raises(UnsafeUrlError):
        fetcher.fetch(HOME)

    assert backend.calls == [HOME], "the private hop is never requested"


def test_a_redirect_to_a_hostname_behind_a_private_address_is_refused() -> None:
    backend = RecordingBackend(responses={HOME: redirect("https://internal.test/")})
    fetcher = build(backend, resolves_to=[PUBLIC_IP])

    def resolver(host: str, port: int) -> Sequence[str]:
        return [PUBLIC_IP] if host == "example.test" else ["10.0.0.7"]

    fetcher.resolver = resolver
    with pytest.raises(UnsafeUrlError):
        fetcher.fetch(HOME)


def test_redirects_stop_at_the_configured_maximum() -> None:
    backend = RecordingBackend(default=redirect("https://example.test/next"))
    fetcher = build(backend, audit_max_redirects=3)

    outcome = fetcher.fetch(HOME)

    assert len(backend.calls) == 4, "the first request plus three hops"
    assert outcome.status_code == 301


# --- caps, content types and TLS ------------------------------------------------------


def test_the_default_cap_is_two_megabytes() -> None:
    """The number the spec fixes, so a change to it is a deliberate one."""
    from pydantic import SecretStr

    assert Settings(jwt_secret=SecretStr("x" * 32)).audit_max_bytes == 2_000_000


def test_a_body_over_the_cap_is_cut_off_and_marked_truncated() -> None:
    backend = RecordingBackend(default=html("x" * 100, truncated=True))
    fetcher = build(backend)

    outcome = fetcher.fetch(HOME)

    assert outcome.truncated is True


def test_the_network_backend_stops_reading_at_the_cap() -> None:
    import httpx

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"a" * 5000
        )
    )
    backend = fetch_backends.NetworkFetchBackend(
        httpx.Client(transport=transport, follow_redirects=False)
    )

    response = backend.get(
        FetchRequest(
            url=HOME,
            parts=split_safe_url(HOME),
            headers={},
            connect_timeout=1.0,
            read_timeout=1.0,
            max_bytes=1000,
        )
    )

    assert len(response.body) == 1000
    assert response.truncated is True


def test_a_non_html_answer_is_recorded_but_not_parsed() -> None:
    backend = RecordingBackend(
        default=BackendResponse(
            status_code=200, headers={"content-type": "application/pdf"}, body=b"%PDF-1.4"
        )
    )
    outcome = build(backend).fetch(HOME)

    assert outcome.status_code == 200
    assert outcome.content_type == "application/pdf"
    assert outcome.parsed is False
    assert outcome.text is None


def test_a_tls_failure_is_a_result_and_is_never_retried() -> None:
    backend = RecordingBackend(default=TlsVerificationError("certificate verify failed"))
    fetcher = build(backend)

    outcome = fetcher.fetch(HOME)

    assert outcome.tls_valid is False
    assert outcome.error_kind == "tls"
    assert len(backend.calls) == 1, "a TLS failure is never retried, with or without verify"


def test_no_module_ever_disables_certificate_verification() -> None:
    """The rule is absolute, so it is asserted against the source itself."""
    for module in (safe_fetch, fetch_backends):
        source = inspect.getsource(module)
        assert "verify=False" not in source
        assert "verify = False" not in source


def test_a_timeout_is_a_result() -> None:
    backend = RecordingBackend(default=FetchTimeoutError("ReadTimeout"))

    outcome = build(backend).fetch(HOME)

    assert outcome.error_kind == "timeout"
    assert outcome.reachable is False


def test_the_total_time_budget_ends_a_long_redirect_chain() -> None:
    clock = FakeClock()

    def slow(request: FetchRequest) -> BackendResponse:
        clock.sleep(15.0)
        return redirect("https://example.test/next")

    backend = RecordingBackend()
    backend.get = slow  # type: ignore[method-assign]
    fetcher = build(backend, clock=clock, audit_total_timeout_seconds=20.0, audit_max_redirects=3)

    outcome = fetcher.fetch(HOME)

    assert outcome.error_kind == "timeout"


def test_the_user_agent_identifies_the_bot_and_a_contact() -> None:
    fetcher = build(RecordingBackend(default=html("<html></html>")))

    assert fetcher.user_agent == "LeadDiscoveryRadarBot/0.4 (+https://agency.example/bot)"


# --- robots.txt -----------------------------------------------------------------------

DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
ALLOW_ALL = "User-agent: *\nDisallow:\n"


def test_robots_disallowing_everything_blocks_the_fetch() -> None:
    backend = RecordingBackend(
        responses={"https://example.test/robots.txt": robots(DISALLOW_ALL)},
        default=html("<html></html>"),
    )
    fetcher = build(backend)

    decision = fetcher.robots(HOME)

    assert decision.allowed is False
    assert decision.status_code == 200
    assert fetcher.requested == [], "a robots check is not a page request"


def test_robots_naming_our_token_is_obeyed() -> None:
    body = "User-agent: LeadDiscoveryRadarBot\nDisallow: /\n\nUser-agent: *\nDisallow:\n"

    assert robots_allows(body, HOME, "LeadDiscoveryRadarBot/0.4 (+x)") is False
    assert robots_allows(ALLOW_ALL, HOME, "LeadDiscoveryRadarBot/0.4 (+x)") is True


def test_robots_404_means_allowed() -> None:
    backend = RecordingBackend(
        responses={"https://example.test/robots.txt": robots("", status=404)}
    )

    decision = build(backend).robots(HOME)

    assert decision.allowed is True
    assert "404" in decision.reason


def test_robots_503_means_disallowed() -> None:
    backend = RecordingBackend(
        responses={"https://example.test/robots.txt": robots("", status=503)}
    )

    decision = build(backend).robots(HOME)

    assert decision.allowed is False
    assert "503" in decision.reason


def test_a_robots_network_error_means_disallowed() -> None:
    backend = RecordingBackend(default=ConnectFailedError("ConnectError"))

    decision = build(backend).robots(HOME)

    assert decision.allowed is False
    assert "could not be read" in decision.reason


def test_a_robots_tls_failure_is_reported_as_a_tls_failure() -> None:
    backend = RecordingBackend(default=TlsVerificationError("certificate verify failed"))

    decision = build(backend).robots(HOME)

    assert decision.allowed is False
    assert decision.tls_valid is False


def test_robots_is_cached_per_host() -> None:
    shared = fakeredis.FakeStrictRedis()
    backend = RecordingBackend(responses={"https://example.test/robots.txt": robots(DISALLOW_ALL)})
    first = build(backend, redis_client=shared)
    second = build(backend, redis_client=shared)

    assert first.robots(HOME).allowed is False
    assert first.robots(HOME).from_cache is True
    assert second.robots("https://example.test/pricing").from_cache is True
    assert len(backend.calls) == 1, "one robots.txt request serves every later check"


# --- politeness -----------------------------------------------------------------------


def test_one_host_is_asked_at_most_once_per_interval() -> None:
    clock = FakeClock()
    throttle = HostThrottle(
        fakeredis.FakeStrictRedis(), interval_seconds=5.0, clock=clock, sleeper=clock.sleep
    )

    assert throttle.wait("example.test") == 0.0
    assert throttle.wait("example.test") == pytest.approx(5.0)
    assert throttle.wait("other.test") == 0.0, "a different host waits for nothing"
    assert clock.delays == [pytest.approx(5.0)]


def test_the_throttle_is_shared_between_fetchers() -> None:
    clock = FakeClock()
    shared = fakeredis.FakeStrictRedis()
    backend = RecordingBackend(default=html("<html></html>"))
    first = build(backend, redis_client=shared, clock=clock, audit_host_throttle_seconds=5.0)
    second = build(backend, redis_client=shared, clock=clock, audit_host_throttle_seconds=5.0)

    first.fetch(HOME)
    second.fetch(HOME)

    assert clock.delays == [pytest.approx(5.0)]


def test_the_concurrency_guard_releases_its_slot() -> None:
    shared = fakeredis.FakeStrictRedis()
    backend = RecordingBackend(default=html("<html></html>"))
    fetcher = build(backend, redis_client=shared, audit_max_concurrency=1)

    fetcher.fetch(HOME)
    fetcher.fetch(HOME)

    assert int(cast(bytes, shared.get("audit:concurrency")) or 0) == 0


# --- pinned connections (v0.8.0): DNS cannot change between the check and the socket -----


def test_the_connection_is_pinned_to_the_validated_address() -> None:
    backend = RecordingBackend(default=html("<html><body>ok</body></html>"))
    fetcher = build(backend, resolves_to=["2606:2800:220:1:248:1893:25c8:1946", PUBLIC_IP])

    outcome = fetcher.fetch(HOME)

    assert outcome.status_code == 200
    assert [r.pinned_ip for r in backend.requests] == [PUBLIC_IP], "IPv4 preferred, as text"
    assert backend.requests[0].parts.hostname == "example.test", "the name stays for SNI/Host"


def test_a_resolver_that_changes_its_answer_cannot_redirect_the_socket() -> None:
    """DNS rebinding: the first lookup says public, every later one says private.

    The fetcher resolves once, validates that answer and pins the connection to it, so
    the backend is handed the public address and the second answer is never consulted.
    """
    backend = RecordingBackend(default=html("<html><body>ok</body></html>"))
    fetcher = build(backend)
    answers = iter([[PUBLIC_IP], ["10.0.0.7"], ["10.0.0.7"]])
    lookups: list[str] = []

    def rebinding_resolver(host: str, port: int) -> Sequence[str]:
        lookups.append(host)
        return next(answers)

    fetcher.resolver = rebinding_resolver

    outcome = fetcher.fetch(HOME)

    assert outcome.status_code == 200
    assert lookups == ["example.test"], "exactly one lookup per request"
    assert backend.requests[0].pinned_ip == PUBLIC_IP

    # The very next request resolves again — and this time the answer is private, so it
    # is refused before any socket opens. Nothing ever connected to 10.0.0.7.
    with pytest.raises(UnsafeUrlError, match="private"):
        fetcher.fetch(HOME)
    assert len(backend.requests) == 1


def test_an_ip_literal_url_needs_no_pin() -> None:
    backend = RecordingBackend(default=html("<html><body>ok</body></html>"))
    fetcher = build(backend)
    fetcher.resolver = lambda host, port: pytest.fail("an IP literal is never resolved")

    fetcher.fetch(f"http://{PUBLIC_IP}/")

    assert backend.requests[0].pinned_ip is None


def test_each_redirect_hop_is_pinned_to_its_own_validated_address() -> None:
    backend = RecordingBackend(
        responses={HOME: redirect("https://other.test/"), "https://other.test/": html("<p>x</p>")}
    )
    fetcher = build(backend)
    fetcher.resolver = lambda host, port: (
        [PUBLIC_IP] if host == "example.test" else ["93.184.216.35"]
    )

    fetcher.fetch(HOME)

    assert [(r.parts.hostname, r.pinned_ip) for r in backend.requests] == [
        ("example.test", PUBLIC_IP),
        ("other.test", "93.184.216.35"),
    ]
