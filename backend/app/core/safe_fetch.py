"""The one door every request to a business's own website goes through.

`app/core/http.py` talks to fixed, official API hosts. This module is the opposite case:
the URL comes from data we did not write, so it is treated as hostile until proven
otherwise. The blueprint's rule (slide 42) is "SSRF-guard everything", which here means:

* only `http`/`https`, only ports 80 and 443;
* every hostname is resolved and **every** address it resolves to must be public — a
  name pointing at `169.254.169.254` is refused, not fetched;
* every redirect hop is validated again, so a public URL cannot hand us a private one;
* bodies are streamed and cut off at a cap, so a huge file cannot exhaust memory;
* certificates are verified, and a failure is a *result* — nothing ever retries without
  verification;
* `robots.txt` is fetched first, cached, and obeyed. When it cannot be read at all we
  treat the site as disallowed (RFC 9309's conservative reading);
* one host is asked at most once every few seconds, and the whole process keeps a cap on
  how many audits fetch at the same time.

Known limitation, by design in this spec: the addresses are validated and the connection
is then made by name, so a hostile DNS server could in theory answer differently the
second time. Closing that needs a pinned resolver or an egress proxy, which is v0.8.0.
"""

import hashlib
import ipaddress
import socket
import time
import urllib.robotparser
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

from redis import Redis

from app.core.config import Settings, get_settings
from app.core.fetch_backends import (
    BackendResponse,
    ConnectFailedError,
    FetchBackend,
    FetchError,
    FetchRequest,
    TlsVerificationError,
    default_backends,
)
from app.core.logging import get_logger

logger = get_logger("app.safe_fetch")

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}
ALLOWED_PORTS = frozenset({80, 443})
# Carrier-grade NAT. Python's `is_private` already covers it, but it is listed on its own
# so a refusal says which rule caught the address.
CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
PARSEABLE_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})

ROBOTS_PATH = "/robots.txt"
ROBOTS_CACHE_PREFIX = "audit:robots"
THROTTLE_PREFIX = "audit:throttle"
CONCURRENCY_KEY = "audit:concurrency"
CONCURRENCY_TTL_SECONDS = 60
# How long `acquire` waits for a slot before going ahead anyway. The per-host throttle is
# the promise we make to a site owner; the concurrency cap is only about our own load.
CONCURRENCY_MAX_WAIT_SECONDS = 30.0
CONCURRENCY_POLL_SECONDS = 0.25

Resolver = Callable[[str, int], Sequence[str]]


class UnsafeUrlError(Exception):
    """A URL this system refuses to request. The reason is safe to store and show."""

    def __init__(self, reason: str, *, url: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.url = url


@dataclass(frozen=True)
class FetchOutcome:
    """What one guarded fetch produced. A failure is data here, never an exception."""

    url: str
    final_url: str | None = None
    status_code: int | None = None
    redirect_chain: tuple[str, ...] = ()
    content_type: str | None = None
    body: bytes = b""
    text: str | None = None
    truncated: bool = False
    tls_valid: bool = True
    error: str | None = None
    error_kind: str | None = None

    @property
    def reachable(self) -> bool:
        return self.error is None and self.status_code is not None

    @property
    def parsed(self) -> bool:
        return self.text is not None

    @property
    def html_sha256(self) -> str | None:
        return hashlib.sha256(self.body).hexdigest() if self.body else None


@dataclass(frozen=True)
class RobotsDecision:
    """Whether we may fetch a page, and the evidence for that answer."""

    allowed: bool
    reason: str
    robots_url: str
    status_code: int | None = None
    tls_valid: bool = True
    from_cache: bool = False


# --- the guard, as pure functions so the tests can hit every rule ---------------------


def normalize_url(raw: str) -> str:
    """Give a bare host a scheme so `example.com/x` and `https://example.com/x` agree."""
    value = (raw or "").strip()
    if not value:
        raise UnsafeUrlError("The URL is empty")
    if "//" not in value.split("?", 1)[0]:
        value = f"https://{value}"
    return value


def split_safe_url(raw: str) -> SplitResult:
    """Parse a URL and refuse anything outside the scheme/port/host policy."""
    parts = urlsplit(normalize_url(raw))
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Scheme '{scheme or 'none'}' is not allowed", url=raw)
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("The URL has no host", url=raw)
    try:
        port = parts.port
    except ValueError as exc:  # a port that is not a number at all
        raise UnsafeUrlError("The URL has an invalid port", url=raw) from exc
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeUrlError(f"Port {port} is not allowed; only 80 and 443 are", url=raw)
    if parts.username or parts.password:
        raise UnsafeUrlError("A URL carrying credentials is not fetched", url=raw)
    # An IP literal is validated here; a name is validated once it is resolved.
    literal = _as_ip(host)
    if literal is not None:
        blocked = blocked_reason(literal)
        if blocked is not None:
            raise UnsafeUrlError(f"{host} is {blocked}", url=raw)
    return parts


def port_of(parts: SplitResult) -> int:
    return parts.port or DEFAULT_PORTS[parts.scheme.lower()]


def blocked_reason(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why this address may not be connected to, or `None` when it is a public one."""
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped or _sixtofour(address) or _teredo(address)
        if mapped is not None:
            reason = blocked_reason(mapped)
            return f"an IPv6 form of {mapped} ({reason})" if reason else None
    for label, predicate in (
        ("the unspecified address", address.is_unspecified),
        ("a loopback address", address.is_loopback),
        ("a link-local address", address.is_link_local),
        ("a multicast address", address.is_multicast),
        ("a CGNAT address", address in CGNAT_NETWORK if address.version == 4 else False),
        # Reserved before private: Python counts 240.0.0.0/4 as both, and "reserved" is
        # the more precise thing to tell an operator.
        ("a reserved address", address.is_reserved),
        ("a private address", address.is_private),
    ):
        if predicate:
            return label
    return None


def _sixtofour(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    value = address.sixtofour
    return value if isinstance(value, ipaddress.IPv4Address) else None


def _teredo(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    pair = address.teredo
    return pair[1] if pair else None


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def default_resolver(host: str, port: int) -> Sequence[str]:
    """Every address a name resolves to, as text. Raises `ConnectFailedError` on a DNS error."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ConnectFailedError(f"DNS lookup for '{host}' failed: {type(exc).__name__}") from exc
    return [str(info[4][0]) for info in infos]


# --- politeness -----------------------------------------------------------------------


class HostThrottle:
    """At most one request per host per interval, shared by every process through Redis."""

    def __init__(
        self,
        redis: Redis,
        *,
        interval_seconds: float,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._redis = redis
        self._interval = max(interval_seconds, 0.0)
        self._clock = clock
        self._sleeper = sleeper

    def wait(self, host: str) -> float:
        """Block until this host may be asked again. Returns how long it waited."""
        if self._interval <= 0:
            return 0.0
        key = f"{THROTTLE_PREFIX}:{host.lower()}"
        waited = 0.0
        while True:
            now = self._clock()
            raw = self._redis.get(key)
            last = _as_float(raw)
            remaining = 0.0 if last is None else (last + self._interval) - now
            if remaining <= 0:
                self._redis.set(key, f"{now:.6f}", ex=max(int(self._interval) * 2, 1))
                return waited
            self._sleeper(remaining)
            waited += remaining


def _as_float(raw: object) -> float | None:
    if isinstance(raw, bytes | str):
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            return float(text)
        except ValueError:
            return None
    return None


class ConcurrencyGuard:
    """A process-wide cap on how many audits fetch at once, counted in Redis."""

    def __init__(
        self,
        redis: Redis,
        *,
        limit: int,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        max_wait_seconds: float = CONCURRENCY_MAX_WAIT_SECONDS,
    ) -> None:
        self._redis = redis
        self._limit = max(limit, 1)
        self._clock = clock
        self._sleeper = sleeper
        self._max_wait = max_wait_seconds

    def acquire(self) -> None:
        deadline = self._clock() + self._max_wait
        while True:
            count = int(cast(int, self._redis.incr(CONCURRENCY_KEY)) or 0)
            # The TTL is a self-heal: a worker killed mid-fetch cannot leak a slot forever.
            self._redis.expire(CONCURRENCY_KEY, CONCURRENCY_TTL_SECONDS)
            if count <= self._limit:
                return
            self._redis.decr(CONCURRENCY_KEY)
            if self._clock() >= deadline:
                logger.warning(
                    "waited for an audit fetch slot and went ahead anyway",
                    extra={"limit": self._limit},
                )
                return
            self._sleeper(CONCURRENCY_POLL_SECONDS)

    def release(self) -> None:
        remaining = int(cast(int, self._redis.decr(CONCURRENCY_KEY)) or 0)
        if remaining < 0:
            self._redis.set(CONCURRENCY_KEY, 0, ex=CONCURRENCY_TTL_SECONDS)


# --- the fetcher ----------------------------------------------------------------------


@dataclass
class SafeFetcher:
    """Fetch a public web page, or explain why we would not.

    Everything it needs is injectable so a test can assert one rule at a time without a
    socket, a real clock or a Redis server.
    """

    redis: Redis
    backends: list[FetchBackend] = field(default_factory=list)
    settings: Settings = field(default_factory=get_settings)
    resolver: Resolver = default_resolver
    clock: Callable[[], float] = time.time
    sleeper: Callable[[float], None] = time.sleep
    throttle: HostThrottle | None = None
    concurrency: ConcurrencyGuard | None = None

    def __post_init__(self) -> None:
        if not self.backends:
            self.backends = default_backends(allow_fixtures=self.settings.is_development)
        if self.throttle is None:
            self.throttle = HostThrottle(
                self.redis,
                interval_seconds=self.settings.audit_host_throttle_seconds,
                clock=self.clock,
                sleeper=self.sleeper,
            )
        if self.concurrency is None:
            self.concurrency = ConcurrencyGuard(
                self.redis,
                limit=self.settings.audit_max_concurrency,
                clock=self.clock,
                sleeper=self.sleeper,
            )
        # Every page request this fetcher made, in order. The robots tests assert on it.
        self.requested: list[str] = []

    @property
    def user_agent(self) -> str:
        return self.settings.user_agent

    # --- robots -----------------------------------------------------------------

    def robots(self, url: str) -> RobotsDecision:
        """Whether `url` may be fetched, per the origin's `robots.txt`.

        2xx is obeyed, 4xx means "no rules, go ahead", and anything else — 5xx, a
        timeout, a connection error — means "stay away". A certificate that does not
        verify is reported as such so the caller records a TLS finding rather than
        blaming robots.
        """
        parts = split_safe_url(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        robots_url = f"{origin}{ROBOTS_PATH}"

        cached = self._cached_robots(origin)
        if cached is not None:
            status, body = cached
            return self._decide_robots(parts, robots_url, status, body, from_cache=True)

        try:
            response = self._request(robots_url, record=False)
        except FetchError as exc:
            if isinstance(exc, TlsVerificationError):
                return RobotsDecision(
                    allowed=False,
                    reason=f"the certificate for {parts.hostname} could not be verified",
                    robots_url=robots_url,
                    tls_valid=False,
                )
            return RobotsDecision(
                allowed=False,
                reason=f"robots.txt could not be read ({exc.kind}), so the site is left alone",
                robots_url=robots_url,
            )

        body = response.body.decode("utf-8", errors="replace")
        self._cache_robots(origin, response.status_code, body)
        return self._decide_robots(parts, robots_url, response.status_code, body)

    def _decide_robots(
        self,
        parts: SplitResult,
        robots_url: str,
        status: int,
        body: str,
        *,
        from_cache: bool = False,
    ) -> RobotsDecision:
        if 400 <= status < 500:
            return RobotsDecision(
                allowed=True,
                reason=f"robots.txt answered {status}, so no rules apply",
                robots_url=robots_url,
                status_code=status,
                from_cache=from_cache,
            )
        if status >= 500 or status < 200 or status >= 300:
            return RobotsDecision(
                allowed=False,
                reason=f"robots.txt answered {status}, so the site is left alone",
                robots_url=robots_url,
                status_code=status,
                from_cache=from_cache,
            )

        target = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
        allowed = robots_allows(body, target, self.user_agent)
        reason = (
            f"robots.txt at {robots_url} allows {self.user_agent}"
            if allowed
            else f"robots.txt at {robots_url} disallows {self.user_agent}"
        )
        return RobotsDecision(
            allowed=allowed,
            reason=reason,
            robots_url=robots_url,
            status_code=status,
            from_cache=from_cache,
        )

    def _cached_robots(self, origin: str) -> tuple[int, str] | None:
        raw = self.redis.get(f"{ROBOTS_CACHE_PREFIX}:{origin}")
        if not isinstance(raw, bytes | str):
            return None
        text = raw.decode() if isinstance(raw, bytes) else raw
        status_raw, _, body = text.partition("\n")
        try:
            return int(status_raw), body
        except ValueError:
            return None

    def _cache_robots(self, origin: str, status: int, body: str) -> None:
        self.redis.set(
            f"{ROBOTS_CACHE_PREFIX}:{origin}",
            f"{status}\n{body}",
            ex=self.settings.audit_robots_cache_seconds,
        )

    # --- pages ------------------------------------------------------------------

    def fetch(self, url: str) -> FetchOutcome:
        """Fetch one page, following at most `AUDIT_MAX_REDIRECTS` validated hops.

        Raises `UnsafeUrlError` when the URL — or any hop it leads to — is one this
        system refuses to request. Every other failure comes back inside the outcome.
        """
        start = normalize_url(url)
        current = start
        chain: list[str] = []
        deadline = self.clock() + self.settings.audit_total_timeout_seconds

        for hop in range(self.settings.audit_max_redirects + 1):
            remaining = deadline - self.clock()
            if remaining <= 0:
                return FetchOutcome(
                    url=start,
                    final_url=current,
                    redirect_chain=tuple(chain),
                    error="the total time budget for this fetch ran out",
                    error_kind="timeout",
                )
            try:
                response = self._request(current, read_timeout=remaining)
            except TlsVerificationError as exc:
                return FetchOutcome(
                    url=start,
                    final_url=current,
                    redirect_chain=tuple(chain),
                    tls_valid=False,
                    error=exc.detail,
                    error_kind=exc.kind,
                )
            except FetchError as exc:
                return FetchOutcome(
                    url=start,
                    final_url=current,
                    redirect_chain=tuple(chain),
                    error=exc.detail,
                    error_kind=exc.kind,
                )

            location = response.header("location") if _is_redirect(response.status_code) else None
            if location is None or hop >= self.settings.audit_max_redirects:
                return self._outcome(start, current, chain, response)

            chain.append(current)
            current = _absolute(current, location)
            # The whole point of the hop limit: each new target is validated from scratch,
            # so a public page cannot redirect us onto a private address.
            split_safe_url(current)

        # Unreachable: the loop always returns. Kept explicit for the type checker.
        return FetchOutcome(  # pragma: no cover
            url=start, final_url=current, redirect_chain=tuple(chain), error="too many redirects"
        )

    def _outcome(
        self, start: str, current: str, chain: list[str], response: BackendResponse
    ) -> FetchOutcome:
        content_type = response.header("content-type")
        text = None
        if _is_parseable(content_type):
            text = response.body.decode(_charset(content_type), errors="replace")
        return FetchOutcome(
            url=start,
            final_url=current,
            status_code=response.status_code,
            redirect_chain=tuple(chain),
            content_type=content_type,
            body=response.body,
            text=text,
            truncated=response.truncated,
        )

    # --- the single request every path above shares ------------------------------

    def _request(
        self, url: str, *, read_timeout: float | None = None, record: bool = True
    ) -> BackendResponse:
        parts = split_safe_url(url)
        host = parts.hostname or ""
        backend = self._backend(host)
        if backend.resolves_dns:
            self._validate_dns(host, port_of(parts), url)

        assert self.throttle is not None and self.concurrency is not None
        self.throttle.wait(host)
        self.concurrency.acquire()
        if record:
            self.requested.append(url)
        try:
            return backend.get(
                FetchRequest(
                    url=url,
                    parts=parts,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Encoding": "gzip, deflate",
                    },
                    connect_timeout=self.settings.audit_connect_timeout_seconds,
                    read_timeout=min(
                        self.settings.audit_read_timeout_seconds,
                        read_timeout if read_timeout is not None else float("inf"),
                    ),
                    max_bytes=self.settings.audit_max_bytes,
                )
            )
        finally:
            self.concurrency.release()

    def _backend(self, host: str) -> FetchBackend:
        for backend in self.backends:
            if backend.handles(host):
                return backend
        raise UnsafeUrlError(f"No fetch backend will answer for '{host}'")

    def _validate_dns(self, host: str, port: int, url: str) -> None:
        """Resolve the name and refuse if *any* answer is an address we may not reach."""
        addresses = self.resolver(host, port)
        if not addresses:
            raise ConnectFailedError(f"'{host}' resolved to no addresses")
        for raw in addresses:
            address = _as_ip(raw)
            if address is None:
                raise UnsafeUrlError(f"'{host}' resolved to something that is not an IP", url=url)
            blocked = blocked_reason(address)
            if blocked is not None:
                raise UnsafeUrlError(f"'{host}' resolves to {address}, which is {blocked}", url=url)


def robots_allows(body: str, url: str, user_agent: str) -> bool:
    """Ask `urllib.robotparser` about our token and about `*`.

    `RobotFileParser` already falls back to the `*` group when it finds no group for the
    given agent, so asking for our own token is enough — but the token is asked for
    first and explicitly, which is what a site owner expects when they name us.
    """
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.splitlines())
    token = user_agent.split("/", 1)[0]
    return bool(parser.can_fetch(token, url)) and bool(parser.can_fetch(user_agent, url))


def _is_redirect(status: int) -> bool:
    return status in {301, 302, 303, 307, 308}


def _absolute(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location.strip())


def _is_parseable(content_type: str | None) -> bool:
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() in PARSEABLE_CONTENT_TYPES


def _charset(content_type: str | None) -> str:
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset" and value.strip():
            return value.strip().strip('"').lower()
    return "utf-8"


def build_fetcher(
    *,
    redis: Redis | None = None,
    settings: Settings | None = None,
    **overrides: Any,
) -> SafeFetcher:
    """The production wiring: shared Redis, the configured caps, fixtures in development."""
    from app.core.redis import get_redis

    config = settings or get_settings()
    return SafeFetcher(redis=redis or get_redis(), settings=config, **overrides)
