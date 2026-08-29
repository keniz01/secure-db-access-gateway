import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuthError


@pytest.mark.asyncio
class TestLoginFlow:
    """Test suite for login flow and origin redirect parameters."""

    async def test_login_initiates_redirect(self, client):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0
        mock_auth0.authorize_redirect = AsyncMock(
            return_value=RedirectResponse(url="https://test.auth0.com/authorize")
        )

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/login")
            assert response.status_code == 307
            assert response.headers["location"] == "https://test.auth0.com/authorize"

    async def test_login_with_valid_custom_redirect_origin(self, client):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0
        mock_auth0.authorize_redirect = AsyncMock(
            return_value=RedirectResponse(url="https://test.auth0.com/authorize")
        )

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/login?redirect_origin=http://localhost:5173")
            assert response.status_code == 307
            # Verify authorize_redirect was called with redirect_uri ending in /auth
            mock_auth0.authorize_redirect.assert_called_once()
            call_args = mock_auth0.authorize_redirect.call_args
            assert call_args[0][1] == "http://localhost:5173/auth"

    async def test_login_with_rejected_custom_redirect_origin(self, client):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0
        mock_auth0.authorize_redirect = AsyncMock(
            return_value=RedirectResponse(url="https://test.auth0.com/authorize")
        )

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/login?redirect_origin=http://malicious.com")
            assert response.status_code == 307
            # Verify rejected origin falls back to allowed frontend origin
            call_args = mock_auth0.authorize_redirect.call_args
            assert "malicious.com" not in call_args[0][1]

    async def test_login_error_handling(self, client):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0
        mock_auth0.authorize_redirect.side_effect = Exception("OAuth Redirect Error")

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/login")
            assert response.status_code == 500
            data = response.json()
            assert data["detail"] == "Failed to initiate login"
            assert "OAuth Redirect Error" in data["error"]


@pytest.mark.asyncio
class TestCallbackFlow:
    """Test suite for OAuth callback handling."""

    async def test_callback_success(self, client, mocker):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0

        token = {
            "access_token": "valid-access-token-xyz",
            "userinfo": {
                "sub": "auth0|123456",
                "email": "jane.doe@example.com",
                "name": "Jane Doe",
            },
        }
        mock_auth0.authorize_access_token = AsyncMock(return_value=token)

        mock_session = {}
        mocker.patch(
            "starlette.requests.Request.session",
            new_callable=mocker.PropertyMock,
            return_value=mock_session,
        )

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/auth?code=valid-code")
            assert response.status_code == 200
            data = response.json()
            assert data["access_token"] == "valid-access-token-xyz"
            assert data["user"]["email"] == "jane.doe@example.com"
            assert data["user"]["id"] == "auth0|123456"
            assert mock_session["access_token"] == "valid-access-token-xyz"
            assert mock_session["user"]["email"] == "jane.doe@example.com"

    async def test_callback_missing_userinfo(self, client):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0

        # Token response without userinfo key
        token = {"access_token": "token-without-userinfo"}
        mock_auth0.authorize_access_token = AsyncMock(return_value=token)

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/auth?code=some-code")
            assert response.status_code == 401
            data = response.json()
            assert data["detail"] == "access_denied"
            assert "user information" in data["error"]

    async def test_callback_oauth_error(self, client):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0

        mock_auth0.authorize_access_token.side_effect = OAuthError(
            error="invalid_grant", description="Invalid authorization code"
        )

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/auth?error=invalid_grant")
            assert response.status_code == 401
            data = response.json()
            assert data["detail"] == "invalid_grant"
            assert data["error_description"] == "Invalid authorization code"

    async def test_callback_unexpected_exception(self, client):
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0

        mock_auth0.authorize_access_token.side_effect = RuntimeError("Database/Network timeout")

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/auth?code=test-code")
            assert response.status_code == 401
            data = response.json()
            assert data["detail"] == "access_denied"
            assert "Database/Network timeout" in data["error"]


@pytest.mark.asyncio
class TestSessionVerificationFlow:
    """Test suite for session verification and user endpoints."""

    async def test_session_verification_authenticated(self, client, authenticated_session):
        response = await client.get("/api/user")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == authenticated_session["user"]["email"]
        assert data["id"] == authenticated_session["user"]["id"]
        assert data["name"] == authenticated_session["user"]["name"]

    async def test_session_verification_unauthenticated(self, client):
        response = await client.get("/api/user")
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"


@pytest.mark.asyncio
class TestLogoutFlow:
    """Test suite for logout functionality."""

    async def test_logout_clears_session_and_redirects(self, client, mocker):
        mock_session = {"user": {"email": "user@example.com"}, "access_token": "token123"}
        mocker.patch(
            "starlette.requests.Request.session",
            new_callable=mocker.PropertyMock,
            return_value=mock_session,
        )

        response = await client.get("/api/logout")
        assert response.status_code == 307
        location = response.headers["location"]
        assert "logout" in location
        assert "client_id=" in location
        assert "returnTo=" in location
        assert mock_session == {}


@pytest.mark.asyncio
class TestExpiredTokenAndUnauthenticatedProtection:
    """Test suite for expired-token and unauthenticated access across protected routes."""

    @pytest.mark.parametrize(
        "endpoint,method,json_payload",
        [
            ("/api/user", "GET", None),
            ("/api/dashboard", "GET", None),
            ("/api/text-to-sql", "POST", {"query": "SELECT * FROM users"}),
        ],
    )
    async def test_protected_endpoints_reject_unauthenticated_requests(
        self, client, endpoint, method, json_payload
    ):
        if method == "GET":
            response = await client.get(endpoint)
        else:
            response = await client.post(endpoint, json=json_payload)

        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"
