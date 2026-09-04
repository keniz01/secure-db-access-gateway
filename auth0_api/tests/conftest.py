import pytest
import asyncio
from typing import AsyncGenerator, Generator
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch

from app.factory import create_app
from app.services.ai_service import AIService
from app.config.settings import settings

@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def mock_settings():
    """Mock settings for all tests."""
    with patch.multiple(settings, 
                        AUTH0_DOMAIN="test-auth0-domain",
                        AUTH0_CLIENT_ID="test-client-id",
                        SESSION_SECRET_KEY="test-secret",
                        APP_SECRET_KEY="test-secret"):
        yield settings

@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI application instance for testing."""
    return create_app()

@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create an HTTP client for testing the FastAPI application."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False
    ) as client:
        yield client

@pytest.fixture
def mock_ai_service():
    """Mock the AI service."""
    mock_instance = MagicMock(spec=AIService)
    mock_instance.get_greeting = AsyncMock(return_value="Mocked AI greeting")
    mock_instance.generate_embeddings = AsyncMock(return_value=[0.1] * 768)
    
    with patch("app.services.ai_service.get_ai_service", return_value=mock_instance), \
         patch("app.routes.user_routes.get_ai_service", return_value=mock_instance):
        yield mock_instance

@pytest.fixture
def authenticated_session(mocker):
    """Fixture to mock an authenticated session."""
    # We'll use this in individual tests to mock the session data
    mock_session_data = {
        "user": {
            "id": "test-user-id",
            "email": "test@example.com",
            "name": "Test User"
        },
        "access_token": "test-access-token"
    }
    
    # Patch the session attribute of Starlette's Request
    # Note: This is a bit brute-force but effective for FastAPI/Starlette tests
    mocker.patch("starlette.requests.Request.session", new_callable=mocker.PropertyMock, return_value=mock_session_data)
    return mock_session_data
