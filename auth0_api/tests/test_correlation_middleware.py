import logging
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.config.logging import (
    CorrelationIdFilter,
    set_current_correlation_id,
)
from app.services.text_to_sql_service import TextToSqlService


@pytest.mark.asyncio
async def test_auto_generated_correlation_id(client) -> None:
    """A request without correlation headers receives generated X-Correlation-ID and X-Request-ID."""
    response = await client.get("/healthz")
    assert response.status_code == 200

    cid = response.headers.get("x-correlation-id")
    rid = response.headers.get("x-request-id")
    assert cid is not None
    assert rid is not None
    assert cid == rid
    # Should parse as valid UUID
    uuid.UUID(cid)


@pytest.mark.asyncio
async def test_preserve_incoming_x_correlation_id(client) -> None:
    """An incoming X-Correlation-ID is preserved and echoed on the response."""
    test_id = "test-correlation-123"
    response = await client.get("/healthz", headers={"X-Correlation-ID": test_id})
    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == test_id
    assert response.headers.get("x-request-id") == test_id


@pytest.mark.asyncio
async def test_preserve_incoming_x_request_id(client) -> None:
    """An incoming X-Request-ID is preserved and echoed on the response."""
    test_id = "test-request-456"
    response = await client.get("/healthz", headers={"X-Request-ID": test_id})
    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == test_id
    assert response.headers.get("x-request-id") == test_id


@pytest.mark.asyncio
async def test_graphql_proxy_propagates_correlation_headers(client, mocker) -> None:
    """The GraphQL BFF proxy forwards correlation headers to sql_query_api."""
    mock_upstream = MagicMock()
    mock_upstream.content = b'{"data": {"ping": "pong"}}'
    mock_upstream.status_code = 200
    mock_upstream.headers = {"content-type": "application/json"}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_upstream)

    mocker.patch(
        "app.routes.graphql_routes.get_authenticated_session",
        return_value={
            "user": {"id": "u1"},
            "access_token": "valid-token",
            "csrf_token": "csrf-tok",
        },
    )
    mocker.patch("app.routes.graphql_routes.httpx.AsyncClient", return_value=mock_client)

    client.cookies.set("csrf_token", "csrf-tok")
    response = await client.post(
        "/api/graphql",
        content=b'{"query": "{ping}"}',
        headers={
            "Origin": "http://localhost:5173",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": "csrf-tok",
            "X-Correlation-ID": "corr-graphql-forward",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == "corr-graphql-forward"
    assert response.headers.get("x-request-id") == "corr-graphql-forward"

    post_kwargs = mock_client.post.call_args.kwargs
    sent_headers = post_kwargs["headers"]
    assert sent_headers["X-Correlation-ID"] == "corr-graphql-forward"
    assert sent_headers["X-Request-ID"] == "corr-graphql-forward"
    assert sent_headers["Authorization"] == "Bearer valid-token"


def test_text_to_sql_api_headers_injects_correlation_id() -> None:
    """_api_headers injects current correlation ID when set."""
    set_current_correlation_id("corr-text-sql-test")
    try:
        headers = TextToSqlService._api_headers("sample-token")
        assert headers["Authorization"] == "Bearer sample-token"
        assert headers["X-Correlation-ID"] == "corr-text-sql-test"
        assert headers["X-Request-ID"] == "corr-text-sql-test"
    finally:
        set_current_correlation_id("N/A")


def test_correlation_id_filter_attaches_to_log_record() -> None:
    """CorrelationIdFilter attaches current correlation ID to logging records."""
    log_filter = CorrelationIdFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )
    set_current_correlation_id("cid-log-test-123")
    try:
        log_filter.filter(record)
        assert record.correlation_id == "cid-log-test-123"
    finally:
        set_current_correlation_id("N/A")
