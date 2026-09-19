"""Where a guarded fetch actually gets its bytes from.

`SafeFetcher` decides whether a URL may be requested at all; a backend is the thing that
then performs the one request it allowed. Splitting them is what lets the whole audit run
offline: the fixture backend answers from checked-in files, so no test and no demo load
has to reach the internet, while the guard above it is exercised either way.

Two rules hold for every backend:

* redirects are **never** followed here — `SafeFetcher` re-validates each hop itself;
* certificates are **always** verified. A TLS failure is raised as a result
  (`TlsVerificationError`), and nothing anywhere retries the request with verification off.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import SplitResult

import httpx

from app.core.logging import get_logger

logger = get_logger("app.fetch")

# Hosts under this suffix are reserved by RFC 2606 and can never resolve, so a fixture
# for one can never shadow a real website.
RESERVED_SUFFIX = ".invalid"
DEMO_SITES_DIRNAME = "sites"
META_FILENAME = "_meta.json"
ROBOTS_PATH = "/robots.txt"
INDEX_FILENAME = "index.html"
ROBOTS_FILENAME = "robots.txt"
HTML_CONTENT_TYPE = "text/html; charset=utf-8"
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"


class FetchError(Exception):
    """A request that never produced an HTTP answer. Recorded as a result, not a crash."""

    kind = "network"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ConnectFailedError(FetchError):
    kind = "connect"


class FetchTimeoutError(FetchError):
    kind = "timeout"


class TlsVerificationError(FetchError):
    """The certificate did not verify. Never retried without verification."""

    kind = "tls"


@dataclass(frozen=True)
class BackendResponse:
    """One HTTP answer, with its body already capped at the caller's byte limit."""

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    truncated: bool = False

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


@dataclass(frozen=True)
class FetchRequest:
    """Everything a backend needs for exactly one request."""

    url: str
    parts: SplitResult
    headers: Mapping[str, str]
    connect_timeout: float
    read_timeout: float
    max_bytes: int


class FetchBackend(Protocol):
    """One way of getting bytes for a URL."""

    # False for backends that answer from disk: there is no host to resolve, so the DNS
    # half of the SSRF guard has nothing to check (the scheme and port halves still run).
    resolves_dns: bool

    def handles(self, host: str) -> bool: ...

    def get(self, request: FetchRequest) -> BackendResponse: ...


class NetworkFetchBackend:
    """The real one: httpx, certificates verified, redirects not followed."""

    resolves_dns = True

    def __init__(self, client: httpx.Client | None = None) -> None:
        # verify=True is the default and is written out to make it greppable: nothing in
        # this repository may ever construct a client that skips verification.
        self._client = client or httpx.Client(verify=True, follow_redirects=False, trust_env=False)

    def close(self) -> None:
        self._client.close()

    def handles(self, host: str) -> bool:
        """The fallback backend: it answers for anything the others declined."""
        return True

    def get(self, request: FetchRequest) -> BackendResponse:
        timeout = httpx.Timeout(
            connect=request.connect_timeout,
            read=request.read_timeout,
            write=request.read_timeout,
            pool=request.connect_timeout,
        )
        try:
            with self._client.stream(
                "GET",
                request.url,
                headers=dict(request.headers),
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                body, truncated = _read_capped(response, request.max_bytes)
                return BackendResponse(
                    status_code=response.status_code,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    body=body,
                    truncated=truncated,
                )
        except httpx.TimeoutException as exc:
            raise FetchTimeoutError(type(exc).__name__) from exc
        except httpx.ConnectError as exc:
            if _looks_like_tls(exc):
                raise TlsVerificationError(_tls_detail(exc)) from exc
            raise ConnectFailedError(type(exc).__name__) from exc
        except httpx.HTTPError as exc:
            raise ConnectFailedError(type(exc).__name__) from exc


def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Stream the body and stop at `max_bytes`, reporting whether there was more."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in response.iter_bytes():
        remaining = max_bytes - total
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


def _looks_like_tls(exc: Exception) -> bool:
    import ssl

    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__
    text = str(exc).lower()
    return "certificate" in text or "ssl" in text


def _tls_detail(exc: Exception) -> str:
    detail = str(exc).strip()
    return detail or type(exc).__name__


# --- the offline backend -------------------------------------------------------------


def demo_sites_root() -> str:
    """`app/demo/sites`, the directory the checked-in demo websites live in."""
    core_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(core_dir)
    return os.path.join(app_dir, "demo", DEMO_SITES_DIRNAME)


def demo_site_dir(host: str, *, root: str | None = None) -> str | None:
    """The directory holding one demo host's files, or `None` when there is none."""
    base = root or demo_sites_root()
    candidate = os.path.join(base, host.lower())
    # `os.path.join` with a host containing `..` could otherwise walk out of the tree.
    if os.path.commonpath([os.path.abspath(candidate), os.path.abspath(base)]) != os.path.abspath(
        base
    ):
        return None
    return candidate if os.path.isdir(candidate) else None


class FixtureFetchBackend:
    """Answers from `app/demo/sites/<host>/`. Development and tests only.

    It handles two kinds of host:

    * any host with a checked-in directory — that is how the demo builder-site records
      (`*.wixsite.com` and friends) are audited without a request ever leaving the
      machine, which is the whole point of a demo;
    * any `*.invalid` host, whether a fixture exists or not. A reserved name can never
      resolve, so answering "connection refused" for one is exactly what the network
      would do — and it gives the demo its unreachable case.

    Anything else is declined and falls through to the network backend.
    """

    resolves_dns = False

    def __init__(self, root: str | None = None) -> None:
        self._root = root or demo_sites_root()

    def handles(self, host: str) -> bool:
        lowered = host.lower()
        return (
            lowered.endswith(RESERVED_SUFFIX) or demo_site_dir(lowered, root=self._root) is not None
        )

    def get(self, request: FetchRequest) -> BackendResponse:
        host = (request.parts.hostname or "").lower()
        directory = demo_site_dir(host, root=self._root)
        if directory is None:
            raise ConnectFailedError(f"no demo site fixture for '{host}'")

        meta = self._meta(directory)
        path = request.parts.path or "/"
        entry = self._entry(meta, request.parts.scheme, path)

        failure = entry.get("failure") or meta.get("failure")
        if isinstance(failure, str):
            raise _FAILURES.get(failure, ConnectFailedError)(f"{failure} (demo fixture for {host})")

        status = int(entry.get("status", 200))
        headers = {str(k).lower(): str(v) for k, v in dict(entry.get("headers") or {}).items()}

        body = self._body(directory, path, entry)
        if body is None:
            return BackendResponse(status_code=404, headers={"content-type": TEXT_CONTENT_TYPE})
        headers.setdefault(
            "content-type", TEXT_CONTENT_TYPE if path == ROBOTS_PATH else HTML_CONTENT_TYPE
        )
        truncated = len(body) > request.max_bytes
        return BackendResponse(
            status_code=status,
            headers=headers,
            body=body[: request.max_bytes],
            truncated=truncated,
        )

    def _meta(self, directory: str) -> dict[str, Any]:
        path = os.path.join(directory, META_FILENAME)
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}

    def _entry(self, meta: dict[str, Any], scheme: str, path: str) -> dict[str, Any]:
        """Per-path overrides, keyed `"<scheme>:<path>"` first and `"<path>"` second."""
        paths = meta.get("paths")
        if not isinstance(paths, dict):
            return {}
        for key in (f"{scheme}:{path}", path):
            entry = paths.get(key)
            if isinstance(entry, dict):
                return entry
        return {}

    def _body(self, directory: str, path: str, entry: dict[str, Any]) -> bytes | None:
        inline = entry.get("body")
        if isinstance(inline, str):
            return inline.encode()
        # Homepage-only by design: every other path reads the one index file, because a
        # fixture is here to exercise the audit, not to simulate a whole site.
        name = ROBOTS_FILENAME if path == ROBOTS_PATH else INDEX_FILENAME
        file_path = os.path.join(directory, name)
        if not os.path.isfile(file_path):
            return None
        with open(file_path, "rb") as handle:
            return handle.read()


_FAILURES: dict[str, type[FetchError]] = {
    "tls": TlsVerificationError,
    "timeout": FetchTimeoutError,
    "connect": ConnectFailedError,
}


def default_backends(*, allow_fixtures: bool) -> list[FetchBackend]:
    """The backend chain, most specific first. Fixtures exist only in development."""
    backends: list[FetchBackend] = []
    if allow_fixtures:
        backends.append(FixtureFetchBackend())
    backends.append(NetworkFetchBackend())
    return backends
