"""The health endpoint reports both dependencies and needs no auth."""

from fastapi.testclient import TestClient


def test_health_reports_db_and_redis(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "db": True, "redis": True}


def test_health_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.headers["X-Request-ID"]


def test_health_echoes_a_supplied_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"
