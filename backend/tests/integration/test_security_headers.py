"""Security headers on every API answer and strict CORS (spec v0.8.0 §5)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from tests.conftest import TEST_PASSWORD, auth_headers

API = "/api/v1"
EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def test_every_response_carries_the_security_headers(client: TestClient) -> None:
    for response in (
        client.get(f"{API}/health"),
        client.get(f"{API}/nowhere"),
        client.post(f"{API}/auth/login", json={"email": "x@example.com", "password": "nope"}),
    ):
        for name, value in EXPECTED.items():
            assert response.headers.get(name) == value, (response.url, name)
        assert "permissions-policy" in response.headers


def test_authenticated_responses_are_not_cacheable(
    client: TestClient, db: Session, sales_user: User
) -> None:
    anonymous = client.get(f"{API}/health")
    assert anonymous.headers.get("cache-control") != "no-store"

    login = client.post(
        f"{API}/auth/login", json={"email": sales_user.email, "password": TEST_PASSWORD}
    )
    assert login.headers.get("cache-control") == "no-store", "it sets the refresh cookie"

    signed_in = client.get(f"{API}/auth/me", headers=auth_headers(client, sales_user))
    assert signed_in.headers.get("cache-control") == "no-store"


def test_hsts_only_over_https(db: Session) -> None:
    from app.main import create_app

    app = create_app()
    with TestClient(app, base_url="http://testserver") as plain:
        assert "strict-transport-security" not in plain.get(f"{API}/health").headers
    with TestClient(app, base_url="https://testserver") as secure:
        value = secure.get(f"{API}/health").headers.get("strict-transport-security", "")
        assert value.startswith("max-age=31536000")


@pytest.fixture
def cors_client(db: Session) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def test_cors_allows_the_configured_origin_with_credentials(cors_client: TestClient) -> None:
    origin = "http://localhost:3000"  # what the test environment configures
    preflight = cors_client.options(
        f"{API}/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers.get("access-control-allow-origin") == origin
    assert preflight.headers.get("access-control-allow-credentials") == "true"

    response = cors_client.get(f"{API}/health", headers={"Origin": origin})
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_rejects_an_unknown_origin(cors_client: TestClient) -> None:
    evil = "http://evil.example"
    preflight = cors_client.options(
        f"{API}/auth/login",
        headers={"Origin": evil, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers

    response = cors_client.get(f"{API}/health", headers={"Origin": evil})
    assert response.status_code == 200, "the API still answers; the browser is what blocks it"
    # Without `Access-Control-Allow-Origin` the browser discards the answer, whatever
    # other CORS headers Starlette adds by default.
    assert "access-control-allow-origin" not in response.headers
