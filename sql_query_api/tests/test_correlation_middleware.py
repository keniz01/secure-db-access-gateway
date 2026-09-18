"""Tests for correlation ID handling across sql_query_api middlewares and logging."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app_factory import create_app
from config.app_logger import (
    log_audit_event,
    set_current_correlation_id,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_auto_generated_correlation_id(client: TestClient) -> None:
    """A request without correlation headers receives generated X-Correlation-ID and X-Request-ID."""
    response = client.get("/healthz")
    assert response.status_code == 200

    cid = response.headers.get("x-correlation-id")
    rid = response.headers.get("x-request-id")
    assert cid is not None
    assert rid is not None
    assert cid == rid
    uuid.UUID(cid)


def test_preserve_incoming_x_correlation_id(client: TestClient) -> None:
    """An incoming X-Correlation-ID is preserved and echoed on both response headers."""
    test_id = "sql-corr-custom-123"
    response = client.get("/healthz", headers={"X-Correlation-ID": test_id})
    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == test_id
    assert response.headers.get("x-request-id") == test_id


def test_preserve_incoming_x_request_id(client: TestClient) -> None:
    """An incoming X-Request-ID is preserved and echoed on both response headers."""
    test_id = "sql-req-custom-456"
    response = client.get("/healthz", headers={"X-Request-ID": test_id})
    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == test_id
    assert response.headers.get("x-request-id") == test_id


def test_log_audit_event_includes_correlation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_audit_event includes the correlation_id in the structured event."""
    test_cid = "audit-cid-789"
    set_current_correlation_id(test_cid)

    captured_messages: list[str] = []
    from config import app_logger

    monkeypatch.setattr(
        app_logger.logger,
        "bind",
        lambda **kwargs: type(
            "LoggerProxy",
            (),
            {"info": lambda self, msg: captured_messages.append(msg)},
        )(),
    )

    try:
        log_audit_event("test_event", org_id="org-test", user="user@example.com")
        assert len(captured_messages) == 1
        parsed = json.loads(captured_messages[0])
        assert parsed["event"] == "test_event"
        assert parsed["correlation_id"] == test_cid
        assert parsed["org_id"] == "org-test"
        assert parsed["user"] == "user@example.com"
    finally:
        set_current_correlation_id("N/A")


def test_cors_preflight_allows_correlation_headers(client: TestClient) -> None:
    """CORS preflight permits and exposes X-Correlation-ID and X-Request-ID."""
    response = client.options(
        "/healthz",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-correlation-id, x-request-id",
        },
    )
    assert response.status_code == 200
    allow_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "x-correlation-id" in allow_headers
    assert "x-request-id" in allow_headers
