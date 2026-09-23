"""Cross-origin and CSRF tests for the Auth0 API BFF.

Browsers authenticate with an httpOnly session cookie. Because the cookie is
attached automatically, state-changing POSTs must additionally prove they come
from the SPA: Origin/Referer allowlist, X-Requested-With marker, and a
double-submit CSRF token (cookie == header == server-side session value).

These tests exercise the BFF through its ASGI test client (httpx.AsyncClient);
the Origin/Referer/Cookie headers are what a real browser would emit. Routes
under test patch the session helper / service class rather than ``httpx``
itself (the test client is itself an ``httpx.AsyncClient``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth import session_store
from app.config.settings import settings
from app.security.csrf import CSRF_COOKIE_NAME

ALLOWED_ORIGIN = "http://localhost:5173"
EVIL_ORIGIN = "http://attacker.example"
SESSION_TOKEN = "server-side-session-token"
CSRF = "csrf-token-a1b2"
BAD_CSRF = "wrong-token"


def _session(**overrides) -> dict:
    data: dict = {
        "user": {
            "id": "u1",
            "email": "a@b.com",
            "org_id": "org-a",
            "roles": ["admin"],
        },
        "access_token": SESSION_TOKEN,
        "csrf_token": CSRF,
    }
    data.update(overrides)
    return data


def _upstream(content: bytes = b'{"data":{"ping":"ok"}}') -> MagicMock:
    u = MagicMock()
    u.content = content
    u.status_code = 200
    u.headers = {"content-type": "application/json"}
    return u


def _inject_csrf_cookie(client, value: str = CSRF) -> None:
    """Mirror the double-submit cookie on the httpx client cookie jar."""
    client.cookies.set(CSRF_COOKIE_NAME, value)


def _auth_headers(**overrides) -> dict:
    """Build browser-like headers for an authenticated state-changing request.

    ``None`` overrides omit the header entirely (e.g. to simulate a browser
    that does not send Origin or the CSRF echo).
    """
    headers: dict = {
        "Origin": ALLOWED_ORIGIN,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-Token": CSRF,
        "Content-Type": "application/json",
    }
    headers.update(overrides)
    return {k: v for k, v in headers.items() if v is not None}


# ---------------------------------------------------------------------------
# GraphQL proxy: authenticated cross-origin POST
# ---------------------------------------------------------------------------
class TestGraphqlCrossOriginPost:

    @pytest.mark.asyncio
    async def test_allowed_origin_post_succeeds(self, client, mocker) -> None:
        content = b'{"data":{"ping":"pong"}}'
        upstream = _upstream(content)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=upstream)

        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        mocker.patch("app.routes.graphql_routes.httpx.AsyncClient", return_value=mock_client)

        _inject_csrf_cookie(client)
        resp = await client.post("/api/graphql", content=content, headers=_auth_headers())
        assert resp.status_code == 200
        assert mock_client.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {SESSION_TOKEN}"

    @pytest.mark.asyncio
    async def test_disallowed_origin_rejected(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(Origin=EVIL_ORIGIN),
        )
        assert resp.status_code == 403
        assert "CSRF protection failed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_null_origin_rejected(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(Origin="null"),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_no_origin_no_referer_rejected(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(Origin=None, Referer=None),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_allowed_referer_when_origin_absent(self, client, mocker) -> None:
        content = b'{"data":{"ping":"ok"}}'
        upstream = _upstream(content)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=upstream)

        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        mocker.patch("app.routes.graphql_routes.httpx.AsyncClient", return_value=mock_client)

        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=content,
            headers=_auth_headers(Origin=None, Referer=f"{ALLOWED_ORIGIN}/dashboard"),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_disallowed_referer_rejected(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(Origin=None, Referer=f"{EVIL_ORIGIN}/malicious"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CSRF double-submit token
# ---------------------------------------------------------------------------
class TestCsrfDoubleSubmit:

    @pytest.mark.asyncio
    async def test_header_missing_rejected(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(**{"X-CSRF-Token": None}),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cookie_missing_rejected(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_header_cookie_mismatch_rejected(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session()))
        _inject_csrf_cookie(client, BAD_CSRF)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_token_not_bound_to_session_rejected(self, client, mocker) -> None:
        """Cookie and header match but the session holds a different value."""
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value=_session(csrf_token=BAD_CSRF)))
        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Unauthenticated access returns 401 before CSRF check
# ---------------------------------------------------------------------------
class TestUnauthenticatedBypassesCsrf:

    @pytest.mark.asyncio
    async def test_no_session_returns_401(self, client) -> None:
        _inject_csrf_cookie(client)
        resp = await client.post(
            "/api/graphql",
            content=b'{"query":"{ping}"}',
            headers=_auth_headers(),
        )
        assert resp.status_code == 401
        assert "Not authenticated" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Text-to-sql cross-origin POST
# ---------------------------------------------------------------------------
class TestTextToSqlCrossOriginPost:

    @pytest.mark.asyncio
    async def test_allowed_origin_post_succeeds(
        self, client, mock_ai_service
    ) -> None:
        fake_service = MagicMock()
        fake_service.generate_sql_from_text = AsyncMock(
            return_value={"sql": "SELECT 1", "schema": "s"}
        )
        with patch("app.routes.user_routes.get_authenticated_session", new=AsyncMock(return_value=_session())), patch("app.routes.user_routes.TextToSqlService", return_value=fake_service):
            _inject_csrf_cookie(client)
            resp = await client.post(
                "/api/text-to-sql",
                json={"query": "hi", "database_id": "d"},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_disallowed_origin_rejected(
        self, client, mock_ai_service
    ) -> None:
        with patch("app.routes.user_routes.get_authenticated_session", new=AsyncMock(return_value=_session())):
            _inject_csrf_cookie(client)
            resp = await client.post(
                "/api/text-to-sql",
                json={"query": "hi", "database_id": "d"},
                headers=_auth_headers(Origin=EVIL_ORIGIN),
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET endpoints are NOT CSRF-gated
# ---------------------------------------------------------------------------
class TestGetNotCsrfGated:

    @pytest.mark.asyncio
    async def test_dashboard_get_works_without_csrf(
        self, client, mocker, mock_ai_service
    ) -> None:
        mocker.patch(
            "starlette.requests.Request.session",
            new_callable=mocker.PropertyMock,
            return_value={
                "user": {
                    "id": "u1",
                    "email": "a@b.com",
                    "org_id": "org-a",
                    "name": "A",
                    "roles": [],
                },
                "access_token": "tok",
            },
        )
        resp = await client.get("/api/dashboard")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cookie flags
# ---------------------------------------------------------------------------
class TestCookieFlags:

    @pytest.mark.asyncio
    async def test_auth_callback_sets_session_and_csrf_cookies(self, client, mocker) -> None:
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0
        mock_auth0.authorize_access_token = AsyncMock(return_value={
            "access_token": "tok",
            "userinfo": {
                "sub": "u1",
                "email": "a@b.com",
                "name": "A",
                "https://app.secure-db-access-gateway.org/tenant_id": "tenant-1",
            },
        })
        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            resp = await client.get("/api/auth?code=code1")
        assert resp.status_code == 200
        set_cookies = resp.headers.get_list("set-cookie")
        gw = next((c for c in set_cookies if c.startswith("gateway_session=")), "")
        csrf = next((c for c in set_cookies if c.startswith("csrf_token=")), "")
        assert gw, "gateway_session cookie not set"
        assert csrf, "csrf_token cookie not set"
        assert "httponly" in gw.lower()
        assert "samesite=lax" in gw.lower()
        assert ("secure" in gw.lower()) == settings.SESSION_COOKIE_SECURE
        assert "httponly" not in csrf.lower(), "csrf cookie must not be HttpOnly"
        assert "samesite=lax" in csrf.lower()
        assert ("secure" in csrf.lower()) == settings.SESSION_COOKIE_SECURE
        csrf_value = csrf.split(";", 1)[0].split("=", 1)[1]
        assert any(
            s.get("csrf_token") == csrf_value for s in session_store._sessions.values()
        )

    @pytest.mark.asyncio
    async def test_logout_clears_csrf_cookie(self, client) -> None:
        _inject_csrf_cookie(client)
        resp = await client.post("/api/logout", headers=_auth_headers())
        # unauthenticated logout still clears (no session) -> 200 with logout_url
        assert resp.status_code in (200, 307)
        set_cookies = resp.headers.get_list("set-cookie")
        assert any(
            ("csrf_token=" in c or "__Host-csrf_token=" in c) and "max-age=0" in c.lower()
            for c in set_cookies
        ), "csrf_token cookie not cleared on logout"

    @pytest.mark.asyncio
    async def test_logout_get_returns_405(self, client) -> None:
        resp = await client.get("/api/logout")
        assert resp.status_code == 405
        assert resp.headers.get("allow") == "POST"


# ---------------------------------------------------------------------------
# End-to-end: login then proxy using real cookie jar
# ---------------------------------------------------------------------------
class TestEndToEndCookieFlow:

    @pytest.mark.asyncio
    async def test_login_then_csrf_proxy_succeeds(self, client, mocker) -> None:
        # The session cookie may be Secure; httpx only sends Secure cookies over
        # HTTPS, so exercise the real cookie-jar flow on an https base URL.
        client.base_url = "https://test"

        # 1. Login
        mock_oauth = MagicMock()
        mock_auth0 = MagicMock()
        mock_oauth.auth0 = mock_auth0
        mock_auth0.authorize_access_token = AsyncMock(return_value={
            "access_token": "upstream-token",
            "userinfo": {
                "sub": "u1",
                "email": "a@b.com",
                "name": "A",
                "https://app.secure-db-access-gateway.org/tenant_id": "tenant-1",
            },
        })
        with patch("app.routes.auth_routes.get_oauth_instance", return_value=mock_oauth):
            login_resp = await client.get("/api/auth?code=code1")
        assert login_resp.status_code == 200
        csrf_value = next(
            (
                c.split(";", 1)[0].split("=", 1)[1]
                for c in login_resp.headers.get_list("set-cookie")
                if c.startswith("csrf_token=")
            ),
            None,
        )
        assert csrf_value, "no csrf cookie set by /api/auth"

        # 2. Proxy (real cookie jar carries session; we send the token explicitly)
        content = b'{"data":{"ping":"pong"}}'
        upstream = _upstream(content)
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=upstream)
        mocker.patch("app.routes.graphql_routes.httpx.AsyncClient", return_value=mock_client)

        proxy_resp = await client.post(
            "/api/graphql",
            content=content,
            headers={
                "Origin": ALLOWED_ORIGIN,
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-Token": csrf_value,
                "Content-Type": "application/json",
            },
        )
        assert proxy_resp.status_code == 200
        assert proxy_resp.content == content
        assert mock_client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer upstream-token"


# ---------------------------------------------------------------------------
# CORS preflight
# ---------------------------------------------------------------------------
class TestPreflight:

    @pytest.mark.asyncio
    async def test_preflight_allowed_origin(self, client) -> None:
        resp = await client.options(
            "/api/graphql",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-requested-with,x-csrf-token",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
        assert resp.headers.get("access-control-allow-credentials") == "true"
        assert "post" in resp.headers.get("access-control-allow-methods", "").lower()
        assert "x-csrf-token" in resp.headers.get("access-control-allow-headers", "").lower()

    @pytest.mark.asyncio
    async def test_preflight_disallowed_origin(self, client) -> None:
        resp = await client.options(
            "/api/graphql",
            headers={
                "Origin": EVIL_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code in (400, 403)
        assert "access-control-allow-origin" not in {
            k.lower() for k in resp.headers.keys()
        }