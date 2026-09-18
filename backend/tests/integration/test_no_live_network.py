"""The suite must fail loudly rather than quietly call a real API.

Every test runs inside an autouse respx router. This proves the router is doing its job,
so a future adapter cannot accidentally reach the internet from CI.
"""

import httpx
import pytest
import respx


def test_an_unmocked_outbound_call_fails_the_test(mock_http: respx.MockRouter) -> None:
    with pytest.raises(respx.models.AllMockedAssertionError):
        httpx.get("https://places.googleapis.com/v1/places:searchText")


def test_a_mocked_call_goes_through(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://places.googleapis.com/health").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    assert httpx.get("https://places.googleapis.com/health").json() == {"ok": True}


def test_the_asgi_test_client_is_not_intercepted(client: object) -> None:
    """The API's own test client must keep working under the same router."""
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    assert client.get("/api/v1/health").status_code == 200
