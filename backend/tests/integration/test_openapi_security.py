"""The /docs Authorize button.

v0.2.0 had no way to authorize in Swagger, because no security scheme reached the schema.
The scheme is declared by the current-user dependency itself, so these assertions fail if
that wiring is ever removed.
"""

from typing import Any

from fastapi.testclient import TestClient


def schema(client: TestClient) -> dict[str, Any]:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_the_openapi_schema_declares_a_bearer_scheme(client: TestClient) -> None:
    schemes = schema(client)["components"]["securitySchemes"]

    assert schemes, "without a security scheme /docs shows no Authorize button"
    bearer = next(s for s in schemes.values() if s.get("type") == "http")
    assert bearer["scheme"] == "bearer"


def test_a_protected_route_requires_that_scheme(client: TestClient) -> None:
    operation = schema(client)["paths"]["/api/v1/businesses"]["get"]

    assert operation.get("security"), "the route must name the scheme, or Authorize does nothing"


def test_an_open_route_does_not(client: TestClient) -> None:
    operation = schema(client)["paths"]["/api/v1/health"]["get"]

    assert not operation.get("security")


def test_the_docs_page_is_served(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_a_token_pasted_into_authorize_still_works(client: TestClient, db: object) -> None:
    """Swagger sends `Authorization: Bearer <token>`, which is what the dependency reads."""
    from sqlalchemy.orm import Session

    from app.modules.auth.models import Role
    from tests.conftest import auth_headers, make_user

    assert isinstance(db, Session)
    user = make_user(db, Role.sales_rep)

    assert client.get("/api/v1/businesses", headers=auth_headers(client, user)).status_code == 200


def test_a_missing_header_still_returns_our_own_error_envelope(client: TestClient) -> None:
    """`auto_error=False` matters: FastAPI's own 403 would break the envelope contract."""
    response = client.get("/api/v1/businesses")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.json()["error"]["request_id"]


def test_a_malformed_header_is_unauthenticated_not_a_crash(client: TestClient) -> None:
    response = client.get("/api/v1/businesses", headers={"Authorization": "Basic abc"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
