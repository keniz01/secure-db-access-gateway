"""Health endpoint tests for the SQL query gateway."""

import pytest
from fastapi.testclient import TestClient

from app_factory import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_liveness_endpoint(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_endpoint(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_fails_without_tenant_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TENANT_DATABASES_JSON", raising=False)
    response = client.get("/readyz")
    assert response.status_code == 503
