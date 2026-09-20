"""The network backend really connects to the pinned address (spec v0.8.0 §5).

respx sees the request exactly as httpx would send it: the URL host is the validated IP,
the `Host` header carries the site's name, and for https the `sni_hostname` extension
carries it too, so TLS presents the name and checks the certificate against it.
"""

from urllib.parse import urlsplit

import httpx
import respx

from app.core.fetch_backends import FetchRequest, NetworkFetchBackend, pin_connection

PUBLIC_IP = "93.184.216.34"


def _request(url: str, pinned_ip: str | None) -> FetchRequest:
    return FetchRequest(
        url=url,
        parts=urlsplit(url),
        headers={"User-Agent": "LeadDiscoveryRadarBot/0.4 (+test)"},
        connect_timeout=1.0,
        read_timeout=1.0,
        max_bytes=1000,
        pinned_ip=pinned_ip,
    )


def test_pin_connection_rewrites_host_header_and_sni() -> None:
    url, headers, extensions = pin_connection(_request("https://example.test/a?b=1", PUBLIC_IP))
    assert url == f"https://{PUBLIC_IP}/a?b=1"
    assert headers["Host"] == "example.test"
    assert extensions == {"sni_hostname": "example.test"}


def test_pin_connection_keeps_an_explicit_port_and_brackets_ipv6() -> None:
    url, headers, extensions = pin_connection(_request("http://example.test:80/", "2001:db8::10"))
    assert url == "http://[2001:db8::10]:80/"
    assert headers["Host"] == "example.test:80"
    assert extensions == {}, "no SNI over plain http"


def test_pin_connection_leaves_an_unpinned_request_alone() -> None:
    request = _request("https://example.test/", None)
    assert pin_connection(request) == (request.url, dict(request.headers), {})


@respx.mock
def test_the_network_backend_sends_the_request_to_the_pinned_address() -> None:
    route = respx.get(f"https://{PUBLIC_IP}/page").mock(
        return_value=httpx.Response(200, text="<p>hi</p>", headers={"content-type": "text/html"})
    )
    by_name = respx.get("https://example.test/page").mock(return_value=httpx.Response(500))

    response = NetworkFetchBackend().get(_request("https://example.test/page", PUBLIC_IP))

    assert response.status_code == 200
    assert route.called and not by_name.called, "the socket went to the address, not the name"
    sent = route.calls.last.request
    assert sent.headers["host"] == "example.test"
    assert sent.extensions.get("sni_hostname") == "example.test"
    assert sent.url.host == PUBLIC_IP
