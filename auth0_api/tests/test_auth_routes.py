import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.auth.session_store import create_session, get_session, revoke_session
from app.security.csrf import CSRF_COOKIE_NAME

ALLOWED_ORIGIN = "http://localhost:5173"
CSRF_TOKEN = "test-csrf-token"

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
    from fastapi.responses import RedirectResponse
    mock_auth0.authorize_redirect = AsyncMock(return_value=RedirectResponse(url="https://auth0.com/login"))
    
    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/login")
        assert response.status_code == 307
        assert response.headers["location"] == "https://auth0.com/login"

@pytest.mark.asyncio
async def test_logout(client):
    """Test logout POST clears session and returns logout_url (CSRF-protected)."""
    response = await client.post("/api/logout", headers={"Origin": ALLOWED_ORIGIN, "X-Requested-With": "XMLHttpRequest"})
    assert response.status_code == 200
    assert "logout_url" in response.json()
    assert "auth0" in response.json()["logout_url"]


@pytest.mark.asyncio
async def test_logout_get_returns_405(client):
    """GET /api/logout is deprecated — must be POST per OWASP ASVS 4.3.1."""
    response = await client.get("/api/logout")
    assert response.status_code == 405
    assert response.headers.get("allow") == "POST"

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
            "name": "Test User",
            "https://app.secure-db-access-gateway.org/tenant_id": "tenant-test",
        }
    }
    mock_auth0.authorize_access_token = AsyncMock(return_value=token)
    
    mock_session = {}
    mocker.patch("starlette.requests.Request.session", new_callable=mocker.PropertyMock, return_value=mock_session)
    
    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/auth?code=test-code")
        assert response.status_code == 200
        data = response.json()
        assert "access_token" not in data
        assert data["user"]["email"] == "test@example.com"
        assert set(mock_session) == {"session_id"}
        sess = await get_session(mock_session["session_id"])
        assert sess["access_token"] == "test-token"
        await revoke_session(mock_session["session_id"])

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
async def test_auth_callback_rejects_missing_tenant_claim(client, mocker):
    mock_oauth = MagicMock()
    mock_auth0 = MagicMock()
    mock_oauth.auth0 = mock_auth0
    mock_auth0.authorize_access_token = AsyncMock(return_value={
        "access_token": "test-token",
        "userinfo": {
            "sub": "test-sub",
            "email": "test@example.com",
            "name": "Test User",
        },
    })

    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/auth?code=test-code")

    assert response.status_code == 403
    assert response.json()["error"] == "missing_tenant_claim"


@pytest.mark.asyncio
async def test_auth_callback_reads_tenant_claim_from_verified_id_token(client, mocker):
    mock_oauth = MagicMock()
    mock_auth0 = MagicMock()
    mock_oauth.auth0 = mock_auth0
    mock_auth0.authorize_access_token = AsyncMock(return_value={
        "access_token": "test-token",
        "userinfo": {
            "sub": "test-sub",
            "email": "test@example.com",
            "name": "Test User",
        },
        "id_token_claims": {
            "https://app.secure-db-access-gateway.org/tenant_id": "tenant-test",
        },
    })
    mock_session = {}
    mocker.patch("starlette.requests.Request.session", new_callable=mocker.PropertyMock, return_value=mock_session)

    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/auth?code=test-code")

    assert response.status_code == 200
    await revoke_session(mock_session.get("session_id"))


@pytest.mark.asyncio
async def test_graphql_proxy_rejects_missing_session(client):
    """The browser cannot access the SQL API without an authenticated server session."""
    response = await client.post("/api/graphql", json={"query": "query { ping }"}, headers={"X-Requested-With": "XMLHttpRequest"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_graphql_proxy_rejects_requests_without_csrf_header(client):
    """Cookie-authenticated GraphQL requests require the double-submit token."""
    client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)
    with patch(
        "app.routes.graphql_routes.get_authenticated_session",
        new=AsyncMock(return_value={
            "access_token": "test-access-token",
            "csrf_token": CSRF_TOKEN,
        }),
    ):
        response = await client.post(
            "/api/graphql",
            json={"query": "query { ping }"},
            headers={"Origin": ALLOWED_ORIGIN, "X-Requested-With": "XMLHttpRequest"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF protection failed"


@pytest.mark.asyncio
async def test_graphql_proxy_forwards_session_access_token(client, mocker):
    """The SQL API receives the authenticated user's bearer token."""
    mocker.patch(
        "app.routes.graphql_routes.get_authenticated_session",
        new=AsyncMock(return_value={"access_token": "test-access-token", "csrf_token": CSRF_TOKEN}),
    )

    upstream = MagicMock()
    upstream.content = b'{"data":{"ping":"pong"}}'
    upstream.status_code = 200
    upstream.headers = {"content-type": "application/json"}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=upstream)
    mocker.patch("app.routes.graphql_routes.httpx.AsyncClient", return_value=mock_client)
    client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)

    response = await client.post(
        "/api/graphql",
        content=b'{"query":"query { ping }"}',
        headers={
            "Origin": ALLOWED_ORIGIN,
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": CSRF_TOKEN,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    mock_client.post.assert_awaited_once()
    assert mock_client.post.await_args.kwargs["headers"]["Authorization"] == "Bearer test-access-token"


@pytest.mark.asyncio
async def test_auth_callback_rotates_previous_server_session(client, mocker):
    """A fresh login revokes the previously issued server-side session."""
    old_user = {"id": "test-sub", "email": "test@example.com", "name": "Test User", "org_id": "org-1", "roles": []}
    old_session_id = await create_session(old_user, "old-access-token", ttl_seconds=3600)
    assert await get_session(old_session_id) is not None

    mock_oauth = MagicMock()
    mock_auth0 = MagicMock()
    mock_oauth.auth0 = mock_auth0
    mock_auth0.authorize_access_token = AsyncMock(return_value={
        "access_token": "new-token",
        "userinfo": {
            "sub": "test-sub",
            "email": "test@example.com",
            "name": "Test User",
            "https://app.secure-db-access-gateway.org/tenant_id": "org-1",
        },
    })

    mock_session = {"session_id": old_session_id}
    mocker.patch("starlette.requests.Request.session", new_callable=mocker.PropertyMock, return_value=mock_session)

    with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
        response = await client.get("/api/auth?code=test-code")

    assert response.status_code == 200
    assert await get_session(old_session_id) is None
    new_session_id = mock_session.get("session_id")
    assert new_session_id and new_session_id != old_session_id
    sess = await get_session(new_session_id)
    assert sess["access_token"] == "new-token"

    await revoke_session(new_session_id)
