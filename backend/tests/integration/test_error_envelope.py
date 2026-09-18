"""Every error response has the same shape and carries the request id."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from tests.conftest import auth_headers

ENVELOPE_KEYS = {"code", "message", "request_id", "details"}


def assert_envelope(body: dict[str, object], request_id: str) -> None:
    assert set(body) == {"error"}
    error = body["error"]
    assert isinstance(error, dict)
    assert set(error) == ENVELOPE_KEYS
    assert error["request_id"] == request_id
    assert isinstance(error["message"], str) and error["message"]


def test_a_404_uses_the_envelope(client: TestClient, db: Session, sales_user: User) -> None:
    response = client.get(
        "/api/v1/search-jobs/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(client, sales_user),
    )
    assert response.status_code == 404
    assert_envelope(response.json(), response.headers["X-Request-ID"])
    assert response.json()["error"]["code"] == "not_found"


def test_a_401_uses_the_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert_envelope(response.json(), response.headers["X-Request-ID"])


def test_a_422_lists_the_offending_fields(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422
    body = response.json()
    assert_envelope(body, response.headers["X-Request-ID"])
    assert body["error"]["details"]["errors"]


def test_an_unrouted_path_uses_the_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/nothing-here")
    assert response.status_code == 404
    assert_envelope(response.json(), response.headers["X-Request-ID"])


def test_a_wrong_method_uses_the_envelope(client: TestClient) -> None:
    response = client.delete("/api/v1/health")
    assert response.status_code == 405
    assert_envelope(response.json(), response.headers["X-Request-ID"])


def test_the_request_id_is_echoed_into_the_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers={"X-Request-ID": "trace-me-1"})
    assert response.json()["error"]["request_id"] == "trace-me-1"
