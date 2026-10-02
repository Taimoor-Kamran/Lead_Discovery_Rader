"""Two hosts on one IP each get their own connection and their own certificate check (C2).

v0.15.0. httpcore picks a pooled connection by origin alone, and a pinned request's origin
is the IP, so with keep-alive a request for `b.test` could ride a connection opened — and
certificate-checked — for `a.test`. The server here is a loopback TLS server holding one
certificate for both names; it records every connection it accepts and the name each one
asked for in its TLS hello. Nothing leaves the machine: the suite's HTTP guard
(`mock_http`) passes through only the loopback port this server listens on.

The control test runs the same requests through a keep-alive client and shows the reuse,
so the main test is known to be able to fail.
"""

import contextlib
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import respx

from app.core.fetch_backends import (
    FETCH_LIMITS,
    FetchRequest,
    NetworkFetchBackend,
    fetch_client,
)

TLS = Path(__file__).resolve().parents[1] / "fixtures" / "tls"
LOOPBACK = "127.0.0.1"
BODY = b"<p>ok</p>"


class SharedIpServer:
    """One TLS listener on loopback answering every request with a small keep-alive page."""

    def __init__(self) -> None:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(TLS / "server.pem", TLS / "server.key")
        context.sni_callback = self._hello
        self._context = context
        self._listener = socket.create_server((LOOPBACK, 0))
        self.port = self._listener.getsockname()[1]
        self.server_names: list[str | None] = []
        self.connections = 0
        self._lock = threading.Lock()
        self._open: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _hello(self, sock: ssl.SSLObject | ssl.SSLSocket, name: str | None, _: object) -> None:
        with self._lock:
            self.server_names.append(name)

    def _serve(self) -> None:
        while True:
            try:
                raw, _ = self._listener.accept()
            except OSError:
                return
            with self._lock:
                self.connections += 1
            threading.Thread(target=self._answer, args=(raw,), daemon=True).start()

    def _answer(self, raw: socket.socket) -> None:
        try:
            conn = self._context.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            raw.close()
            return
        with self._lock:
            self._open.append(conn)
        buffer = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n\r\n" in buffer:
                    _, buffer = buffer.split(b"\r\n\r\n", 1)
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                        + f"Content-Length: {len(BODY)}\r\n\r\n".encode()
                        + BODY
                    )
        except (ssl.SSLError, OSError):
            return
        finally:
            conn.close()

    def close(self) -> None:
        self._listener.close()
        with self._lock:
            for conn in self._open:
                with contextlib.suppress(OSError):
                    conn.close()


@pytest.fixture
def server(mock_http: respx.MockRouter) -> Iterator[SharedIpServer]:
    """The loopback server, and the one route the suite's HTTP guard lets through to it."""
    running = SharedIpServer()
    mock_http.route(host=LOOPBACK, port=running.port).pass_through()
    yield running
    running.close()


def trusting_test_ca() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(TLS / "ca.pem"))


def request(host: str, port: int) -> FetchRequest:
    url = f"https://{host}:{port}/"
    return FetchRequest(
        url=url,
        parts=urlsplit(url),
        headers={"User-Agent": "LeadDiscoveryRadarBot (+test)"},
        connect_timeout=5.0,
        read_timeout=5.0,
        max_bytes=10_000,
        pinned_ip=LOOPBACK,
    )


def fetch_all(backend: NetworkFetchBackend, port: int, hosts: list[str]) -> None:
    for host in hosts:
        response = backend.get(request(host, port))
        assert response.status_code == 200 and response.body == BODY


def test_two_hosts_on_one_ip_each_get_their_own_connection_and_sni(
    server: SharedIpServer,
) -> None:
    backend = NetworkFetchBackend(fetch_client(verify=trusting_test_ca()))
    try:
        fetch_all(backend, server.port, ["a.test", "b.test", "b.test"])
    finally:
        backend.close()

    assert server.connections == 3, "one connection per request, none reused"
    assert server.server_names == ["a.test", "b.test", "b.test"], (
        "each request's certificate was checked against its own name"
    )


def test_control_a_keep_alive_client_reuses_one_connection_across_both_hosts(
    server: SharedIpServer,
) -> None:
    """What the code did before v0.15.0: `b.test` rode `a.test`'s connection."""
    keep_alive = httpx.Client(verify=trusting_test_ca(), follow_redirects=False, trust_env=False)
    backend = NetworkFetchBackend(keep_alive)
    try:
        fetch_all(backend, server.port, ["a.test", "b.test"])
    finally:
        backend.close()

    assert server.connections == 1
    assert server.server_names == ["a.test"], "b.test's certificate was never checked"


def test_the_default_backend_keeps_no_idle_connection() -> None:
    backend = NetworkFetchBackend()
    try:
        pool = backend._client._transport._pool  # type: ignore[attr-defined]
        assert pool._max_keepalive_connections == 0
        assert FETCH_LIMITS.max_keepalive_connections == 0
    finally:
        backend.close()


def test_the_fetch_client_refuses_to_skip_verification() -> None:
    with pytest.raises(ValueError):
        fetch_client(verify=False)
