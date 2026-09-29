import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi import Request
from fastapi.testclient import TestClient

from config.app_logger import get_current_correlation_id, reset_current_correlation_id, set_current_correlation_id
from middlewares.correlation_middleware import correlation_id_middleware
from services.opa_policy_engine import OpaPolicyEvaluator, OpaConfig
from auth import Principal


def test_correlation_id_from_header():
    """Test that correlation ID is extracted from X-Correlation-ID header."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/graphql",
        "raw_path": b"/graphql",
        "query_string": b"",
        "headers": [(b"x-correlation-id", b"test-correlation-123")],
        "client": ("127.0.0.1", 1234),
        "server": ("internal", 8002),
    }
    request = Request(scope)
    token = set_current_correlation_id("")
    try:
        import asyncio
        async def test():
            async def mock_call_next(req):
                response = MagicMock()
                response.headers = {}
                return response
            result = await correlation_id_middleware(request, mock_call_next)
            assert result.headers["X-Correlation-ID"] == "test-correlation-123"
            assert request.state.correlation_id == "test-correlation-123"
        asyncio.run(test())
    finally:
        reset_current_correlation_id(token)


def test_correlation_id_generated_when_missing():
    """Test that correlation ID is generated when not provided."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/graphql",
        "raw_path": b"/graphql",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("internal", 8002),
    }
    request = Request(scope)
    token = set_current_correlation_id("")
    try:
        import asyncio
        async def test():
            async def mock_call_next(req):
                response = MagicMock()
                response.headers = {}
                return response
            result = await correlation_id_middleware(request, mock_call_next)
            assert "X-Correlation-ID" in result.headers
            assert len(result.headers["X-Correlation-ID"]) == 36  # UUID length
        asyncio.run(test())
    finally:
        reset_current_correlation_id(token)


def test_correlation_id_invalid_format_rejected():
    """Test that invalid correlation ID format is rejected and new one generated."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/graphql",
        "raw_path": b"/graphql",
        "query_string": b"",
        "headers": [(b"x-correlation-id", b"invalid@format!")],
        "client": ("127.0.0.1", 1234),
        "server": ("internal", 8002),
    }
    request = Request(scope)
    token = set_current_correlation_id("")
    try:
        import asyncio
        async def test():
            async def mock_call_next(req):
                response = MagicMock()
                response.headers = {}
                return response
            result = await correlation_id_middleware(request, mock_call_next)
            # Should generate a valid UUID instead
            assert len(result.headers["X-Correlation-ID"]) == 36
        asyncio.run(test())
    finally:
        reset_current_correlation_id(token)


def test_correlation_id_in_error_response():
    """Test that correlation ID is included in error responses."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/graphql",
        "raw_path": b"/graphql",
        "query_string": b"",
        "headers": [(b"x-correlation-id", b"test-error-correlation")],
        "client": ("127.0.0.1", 1234),
        "server": ("internal", 8002),
    }
    request = Request(scope)
    token = set_current_correlation_id("")
    try:
        import asyncio
        async def test():
            async def mock_call_next(req):
                raise Exception("Test error")
            result = await correlation_id_middleware(request, mock_call_next)
            assert result.headers["X-Correlation-ID"] == "test-error-correlation"
            assert result.status_code == 500
        asyncio.run(test())
    finally:
        reset_current_correlation_id(token)


def test_opa_evaluator_includes_correlation_header():
    """Test that OPA evaluator includes correlation ID in requests."""
    config = OpaConfig(url="http://opa:8181", enabled=True)
    evaluator = OpaPolicyEvaluator(config=config)
    
    token = set_current_correlation_id("test-opa-correlation-456")
    try:
        headers = evaluator._get_correlation_header()
        assert headers == {"X-Correlation-ID": "test-opa-correlation-456"}
    finally:
        reset_current_correlation_id(token)


def test_opa_evaluator_no_correlation_header_when_none():
    """Test that OPA evaluator doesn't add header when no correlation ID."""
    config = OpaConfig(url="http://opa:8181", enabled=True)
    evaluator = OpaPolicyEvaluator(config=config)
    
    token = set_current_correlation_id("")
    try:
        headers = evaluator._get_correlation_header()
        assert headers == {}
    finally:
        reset_current_correlation_id(token)


@pytest.mark.asyncio
async def test_opa_query_includes_correlation_header():
    """Test that _query_opa sends correlation header."""
    config = OpaConfig(url="http://opa:8181", enabled=True)
    evaluator = OpaPolicyEvaluator(config=config)
    
    # Mock the client properly
    from unittest.mock import AsyncMock, MagicMock
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"result": {"allowed": True, "reason": "ok", "policy_ids": []}})
    mock_client.post = AsyncMock(return_value=mock_response)
    
    # Patch _get_client to return our mock
    evaluator._get_client = AsyncMock(return_value=mock_client)
    
    token = set_current_correlation_id("test-opa-query-correlation")
    try:
        await evaluator._query_opa("/gateway/evaluate", {"input": "test"})
        
        # Verify the call was made with correlation header
        call_args = mock_client.post.call_args
        assert call_args is not None
        headers = call_args.kwargs.get("headers", {})
        assert "X-Correlation-ID" in headers
        assert headers["X-Correlation-ID"] == "test-opa-query-correlation"
    finally:
        reset_current_correlation_id(token)


@pytest.mark.asyncio
async def test_full_correlation_flow_through_app():
    """Integration test: correlation ID flows through app to OPA."""
    from app_factory import create_app
    
    app = create_app()
    client = TestClient(app)
    
    # Make request with correlation ID
    response = client.post(
        "/graphql",
        json={"query": "query { ping }"},
        headers={"Authorization": "Bearer test-valid-token", "X-Correlation-ID": "full-flow-test-123"}
    )
    
    # Response should include the same correlation ID
    assert response.headers.get("X-Correlation-ID") == "full-flow-test-123"