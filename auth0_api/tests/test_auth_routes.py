import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.auth.session_store import get_session

@pytest.mark.asyncio
async def test_health_check(client):
    """Test health check endpoint."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

@pytest.mark.asyncio
async def test_login_redirect(client, mocker):
    """Test login endpoint initiates OAuth redirect."""
    mock_oauth = MagicMock()
    mock_auth0 = MagicMock()
    mock_oauth.auth0 = mock_auth0
    # authorize_redirect returns a RedirectResponse
    from fastapi.responses import RedirectResponse
    # RedirectResponse defaults to 307
    mock_auth0.authorize_redirect = AsyncMock(return_value=RedirectResponse(url="https://auth0.com/login"))
    
    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/login")
        # FastAPI's RedirectResponse defaults to 307
        assert response.status_code == 307
        assert response.headers["location"] == "https://auth0.com/login"

@pytest.mark.asyncio
async def test_logout(client):
    """Test logout clears session and redirects."""
    # We don't necessarily need to mock the session clear here as it's handled by Starlette
    response = await client.get("/api/logout")
    # RedirectResponse defaults to 307
    assert response.status_code == 307
    assert "logout" in response.headers["location"]
    assert "auth0" in response.headers["location"]

@pytest.mark.asyncio
async def test_auth_callback_success(client, mocker):
    """Test successful auth callback."""
    mock_oauth = MagicMock()
    mock_auth0 = MagicMock()
    mock_oauth.auth0 = mock_auth0
    
    token = {
        "access_token": "test-token",
        "userinfo": {
            "sub": "test-sub",
            "email": "test@example.com",
            "name": "Test User"
        }
    }
    mock_auth0.authorize_access_token = AsyncMock(return_value=token)
    
    # Mock session
    mock_session = {}
    mocker.patch("starlette.requests.Request.session", new_callable=mocker.PropertyMock, return_value=mock_session)
    
    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/auth?code=test-code")
        assert response.status_code == 200
        data = response.json()
        assert "access_token" not in data
        assert data["user"]["email"] == "test@example.com"
        assert set(mock_session) == {"session_id"}
        assert get_session(mock_session["session_id"])["access_token"] == "test-token"

@pytest.mark.asyncio
async def test_auth_callback_error(client, mocker):
    """Test auth callback with OAuth error."""
    mock_oauth = MagicMock()
    mock_auth0 = MagicMock()
    mock_oauth.auth0 = mock_auth0
    
    from authlib.integrations.starlette_client import OAuthError
    mock_auth0.authorize_access_token.side_effect = OAuthError(error="access_denied", description="User denied")
    
    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/auth?error=access_denied")
        assert response.status_code == 401
        data = response.json()
        assert data["detail"] == "access_denied"
        assert data["error_description"] == "User denied"


@pytest.mark.asyncio
async def test_graphql_proxy_rejects_missing_session(client):
    """The browser cannot access the SQL API without an authenticated server session."""
    response = await client.post("/api/graphql", json={"query": "query { ping }"}, headers={"X-Requested-With": "XMLHttpRequest"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_graphql_proxy_rejects_requests_without_csrf_header(client):
    """Cookie-authenticated GraphQL requests require the browser-client marker."""
    response = await client.post("/api/graphql", json={"query": "query { ping }"})

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF protection failed"
