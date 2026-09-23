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
            # OAuth 2.1 PKCE + nonce
            call_kwargs = mock_auth0.authorize_redirect.call_args[1]
            assert "code_challenge" in call_kwargs
            assert call_kwargs["code_challenge_method"] == "S256"
            assert "nonce" in call_kwargs

    async def test_login_with_valid_custom_redirect_origin(self, client):
        """OAuth 2.1: redirect_origin param is deprecated, static redirect_uri used."""
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0
        mock_auth0.authorize_redirect = AsyncMock(
            return_value=RedirectResponse(url="https://test.auth0.com/authorize")
        )

        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            response = await client.get("/api/login?redirect_origin=http://localhost:5173")
            assert response.status_code == 307
            mock_auth0.authorize_redirect.assert_called_once()
            call_args = mock_auth0.authorize_redirect.call_args
            # Static redirect_uri (OAuth 2.1) - param is ignored
            assert call_args[0][1].endswith("/auth")
            assert "code_challenge" in call_args[1]
            assert call_args[1]["code_challenge_method"] == "S256"
            assert "nonce" in call_args[1]

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
            call_args = mock_auth0.authorize_redirect.call_args
            assert "malicious.com" not in call_args[0][1]
            # PKCE still required even for rejected origin
            assert call_args[1]["code_challenge_method"] == "S256"

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
                "https://app.secure-db-access-gateway.org/tenant_id": "tenant-test",
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
            assert "access_token" not in data
            assert data["user"]["email"] == "jane.doe@example.com"
            assert data["user"]["id"] == "auth0|123456"
            assert set(mock_session) == {"session_id"}

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

        response = await client.post("/api/logout")
        assert response.status_code == 200
        body = response.json()
        assert "logout_url" in body
        assert "logout" in body["logout_url"]
        assert "client_id=" in body["logout_url"]
        assert "returnTo=" in body["logout_url"]
        assert mock_session == {}

    async def test_logout_get_returns_405(self, client):
        response = await client.get("/api/logout")
        assert response.status_code == 405


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
