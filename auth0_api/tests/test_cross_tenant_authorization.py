"""
Cross-tenant authorization tests for the Auth0 API.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.auth import session_store
from app.config.settings import settings
from app.security.csrf import CSRF_COOKIE_NAME
from app.services.text_to_sql_service import TextToSqlService

SESSION_TOKEN = "server-side-session-token"
FORGED_TOKEN = "attacker-supplied-token"
SECRET_LIKE = "org-a-secret"
ALLOWED_ORIGIN = "http://localhost:5173"
CSRF_TOKEN = "double-submit-token"


def _csrf_headers() -> dict:
    return {
        "Origin": ALLOWED_ORIGIN,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-Token": CSRF_TOKEN,
    }


def _gql_response(payload: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = payload
    return mock_resp


def _http_401() -> MagicMock:
    request = httpx.Request("POST", "http://sql_query_api/graphql")
    response = httpx.Response(401, request=request, json={"detail": "Authentication required."})
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
    )
    return mock_resp


SCHEMA_OK = _gql_response({"data": {"getTableSchema": {"schema": "CREATE TABLE t (id INT)"}}})


class TestTextToSqlRouteConfusedDeputy:
    @pytest.mark.asyncio
    async def test_route_passes_session_token_and_database_id_to_service(self, client, mock_ai_service) -> None:
        fake_service = MagicMock()
        fake_service.generate_sql_from_text = AsyncMock(return_value={"sql": "SELECT * FROM users", "schema": "schema text"})
        with patch("app.routes.user_routes.get_authenticated_session", new=AsyncMock(return_value={"user": {"id": "u1"}, "access_token": SESSION_TOKEN, "csrf_token": CSRF_TOKEN})), patch("app.routes.user_routes.TextToSqlService", return_value=fake_service):
            client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)
            response = await client.post("/api/text-to-sql", json={"query": "list users", "database_id": "finance"}, headers={**_csrf_headers(), "Authorization": f"Bearer {FORGED_TOKEN}"})
        assert response.status_code == 200
        service_kwargs = fake_service.generate_sql_from_text.call_args.kwargs
        assert service_kwargs["access_token"] == SESSION_TOKEN
        assert service_kwargs["database_id"] == "finance"
        assert FORGED_TOKEN not in str(service_kwargs)

    @pytest.mark.asyncio
    async def test_route_surfaces_upstream_denial_without_rows(self, client, mock_ai_service) -> None:
        denied = {"error": "Unexpected error: Failed to fetch schema: Database is not available for this organisation.", "sql": None}
        fake_service = MagicMock()
        fake_service.generate_sql_from_text = AsyncMock(return_value=denied)
        with patch("app.routes.user_routes.get_authenticated_session", new=AsyncMock(return_value={"user": {"id": "u1"}, "access_token": SESSION_TOKEN, "csrf_token": CSRF_TOKEN})), patch("app.routes.user_routes.TextToSqlService", return_value=fake_service):
            client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)
            response = await client.post("/api/text-to-sql", json={"query": "list users", "database_id": "finance"}, headers=_csrf_headers())
        assert response.status_code == 500
        body = response.json()
        assert "not available for this organisation" in body["error"]
        assert body.get("sql") is None
        assert "results" not in body
        assert body.get("data") is None
        assert SECRET_LIKE not in str(body)


class TestTextToSqlServiceForwarding:
    @pytest.mark.asyncio
    async def test_service_forwards_session_token_and_database_id(self, mock_ai_service) -> None:
        service = TextToSqlService(mock_ai_service)
        mock_ai_service.get_greeting.return_value = "SELECT * FROM users"
        with patch("app.services.text_to_sql_service.httpx.AsyncClient.post", return_value=SCHEMA_OK) as mock_post:
            result = await service.generate_sql_from_text("list users", access_token=SESSION_TOKEN, database_id="finance")
        assert result["sql"] == "SELECT * FROM users"
        post = mock_post.call_args
        assert post.kwargs["headers"]["Authorization"] == f"Bearer {SESSION_TOKEN}"
        assert post.kwargs["json"]["variables"]["databaseId"] == "finance"

    @pytest.mark.asyncio
    async def test_service_cross_tenant_denial_leaks_no_rows(self, mock_ai_service) -> None:
        service = TextToSqlService(mock_ai_service)
        denial = _gql_response({"errors": [{"message": "Database is not available for this organisation."}]})
        with patch("app.services.text_to_sql_service.httpx.AsyncClient.post", return_value=denial):
            result = await service.generate_sql_from_text("list users", access_token=SESSION_TOKEN, database_id="finance")
        assert "not available for this organisation" in result["error"]
        assert result.get("sql") is None
        assert "results" not in result
        assert SECRET_LIKE not in str(result)

    @pytest.mark.asyncio
    async def test_service_execute_round_trip_uses_session_token(self, mock_ai_service) -> None:
        service = TextToSqlService(mock_ai_service)
        mock_ai_service.get_greeting.return_value = "SELECT * FROM t"
        exec_ok = _gql_response({"data": {"executeSqlStatement": [{"id": 1}]}})
        posts: list[dict] = []

        async def fake_post(url, **kwargs):
            posts.append(dict(url=url, **kwargs))
            return SCHEMA_OK if "getTableSchema" in kwargs["json"]["query"] else exec_ok

        with patch("app.services.text_to_sql_service.httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)):
            result = await service.generate_sql_from_text("list users", execute=True, access_token=SESSION_TOKEN, database_id="finance")
        assert result["results"] == [{"id": 1}]
        assert len(posts) == 2
        for post in posts:
            assert post["headers"]["Authorization"] == f"Bearer {SESSION_TOKEN}"
            assert post["json"]["variables"]["databaseId"] == "finance"


class TestGraphqlProxyConfusedDeputy:
    @pytest.mark.asyncio
    async def test_proxy_forwards_session_token_ignoring_client_authorization(self, client, mocker) -> None:
        upstream_content = b'{"data":{"ping":"ok"}}'
        upstream = MagicMock()
        upstream.content = upstream_content
        upstream.status_code = 200
        upstream.headers = {"content-type": "application/json"}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=upstream)
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value={"user": {"id": "u1"}, "access_token": SESSION_TOKEN, "csrf_token": CSRF_TOKEN}))
        mocker.patch("app.routes.graphql_routes.httpx.AsyncClient", return_value=mock_client)
        client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)
        response = await client.post("/api/graphql", content=upstream_content, headers={**_csrf_headers(), "Authorization": f"Bearer {FORGED_TOKEN}", "Content-Type": "application/json"})
        assert response.status_code == 200
        assert response.content == upstream_content
        forward_kwargs = mock_client.post.call_args.kwargs
        assert mock_client.post.call_args.args[0] == settings.SQL_QUERY_API_URL
        assert forward_kwargs["headers"]["Authorization"] == f"Bearer {SESSION_TOKEN}"
        assert FORGED_TOKEN not in str(forward_kwargs["headers"])
        assert forward_kwargs["content"] == upstream_content

    @pytest.mark.asyncio
    async def test_proxy_requires_csrf_header(self, client, mocker) -> None:
        mocker.patch("app.routes.graphql_routes.get_authenticated_session", new=AsyncMock(return_value={"user": {"id": "u1"}, "access_token": SESSION_TOKEN, "csrf_token": CSRF_TOKEN}))
        response = await client.post("/api/graphql", content=b'{"query":"query{ping}"}', headers={"Origin": ALLOWED_ORIGIN, "X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 403
        assert "CSRF protection failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_proxy_rejects_without_server_side_session(self, client) -> None:
        response = await client.post("/api/graphql", content=b'{"query":"query{ping}"}', headers={"X-Requested-With": "XMLHttpRequest", "Authorization": f"Bearer {FORGED_TOKEN}"})
        assert response.status_code == 401
        assert "Not authenticated" in response.json()["detail"]


class TestStaleSessions:
    @pytest.mark.asyncio
    async def test_server_side_session_ttl_expiry_removes_session(self) -> None:
        session_id = await session_store.create_session({"email": "a@b.com"}, "sample-value", ttl_seconds=-1)
        assert await session_store.get_session(session_id) is None
        assert session_id not in session_store._sessions

    @pytest.mark.asyncio
    async def test_revoked_upstream_token_yields_error_not_data(self, mock_ai_service) -> None:
        service = TextToSqlService(mock_ai_service)
        mock_ai_service.get_greeting.return_value = "SELECT * FROM users"
        with patch("app.services.text_to_sql_service.httpx.AsyncClient.post", return_value=_http_401()):
            result = await service.generate_sql_from_text("list users", access_token="revoked-token", database_id="default")
        assert "error" in result
        assert result.get("results") is None
        assert SECRET_LIKE not in str(result)


class TestPrivilegeChanges:
    @pytest.mark.asyncio
    async def test_admin_overview_role_revocation_takes_effect_next_request(self, client, authenticated_session) -> None:
        authenticated_session["user"]["org_id"] = "org-admin"
        authenticated_session["user"]["roles"] = ["admin"]
        with patch("app.routes.user_routes.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"data": {"result": []}})
            first = await client.get("/api/admin/overview")
            assert first.status_code == 200
            assert first.json()["org_id"] == "org-admin"
        authenticated_session["user"]["roles"] = ["viewer"]
        second = await client.get("/api/admin/overview")
        assert second.status_code == 403
        assert second.json()["detail"] == "Administrator role required"
