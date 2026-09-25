"""Is *our* network up? Asked before a site is called unreachable (spec v0.12.0, item 3a).

A homepage that does not answer is one of two very different facts: the business's site is
down, or the machine running the audit has no network — a laptop that slept mid-run, say.
Only the first is something to tell the business. This asks the second question directly.

The probe opens a TCP connection to one fixed host (`AUDIT_CONNECTIVITY_CHECK_URL`, by
default the PageSpeed API host every audit already calls) and closes it. No HTTP request
is sent, so it reads nothing, costs no quota and is invisible to the API. It is a
connection, not a fetch, which is why it lives here and not in `http.py` or `safe_fetch.py`.
"""

import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings

DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class ProbeResult:
    online: bool
    detail: str


class ConnectivityProbe(Protocol):
    def check(self) -> ProbeResult: ...


class TcpConnectivityProbe:
    """Resolve the host and open one TCP connection to it, within a timeout."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        parts = urlsplit(url)
        if not parts.hostname:
            raise ValueError("AUDIT_CONNECTIVITY_CHECK_URL has no host")
        self.host = parts.hostname
        self.port = parts.port or DEFAULT_PORTS.get(parts.scheme.lower(), 443)
        self.timeout = timeout_seconds

    def check(self) -> ProbeResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                pass
        except OSError as exc:
            return ProbeResult(
                online=False,
                detail=f"a connection to {self.host}:{self.port} failed ({type(exc).__name__})",
            )
        return ProbeResult(online=True, detail=f"a connection to {self.host}:{self.port} opened")


def build_probe(settings: Settings | None = None) -> ConnectivityProbe | None:
    """The configured probe, or `None` when `AUDIT_CONNECTIVITY_CHECK_URL` is empty."""
    config = settings or get_settings()
    url = config.audit_connectivity_check_url.strip()
    if not url:
        return None
    return TcpConnectivityProbe(url, timeout_seconds=config.audit_connect_timeout_seconds)
