"""Penetration tests for GraphQL DoS and resource-exhaustion hardening."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app_factory import create_app
from auth import Principal
from middlewares.rate_limit_middleware import RateLimitMiddleware
from repositories.sql_query_repository import SqlQueryRepository
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from routes import sql_query_controller
from services.sql_query_service import SqlQueryService
from services.tenant_database_resolver import TenantDatabaseConfig


@pytest.fixture(scope="module")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an in-memory SQLite async engine seeded with 124 rows."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE artist (id INT PRIMARY KEY, name TEXT, genre TEXT)"))
        await conn.execute(
            text(
                "INSERT INTO artist (id, name, genre) VALUES "
                "(1, 'The Beatles', 'Rock'), "
                "(2, 'Miles Davis', 'Jazz')"
            )
        )
        await conn.execute(text("CREATE TABLE track (id INT PRIMARY KEY, title TEXT)"))
        for i in range(1, 125):
            await conn.execute(
                text("INSERT INTO track (id, title) VALUES (:id, :title)"),
                {"id": i, "title": f"Track {i}"},
            )

    yield engine
    await engine.dispose()


@pytest.fixture
def client(test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with an overridden SQL service bound to the seeded in-memory DB."""
    safety_checker = DefaultSqlSafetyChecker()
    test_repo = SqlQueryRepository(engine=test_engine, sql_safety_checker=safety_checker)
    test_service = SqlQueryService(repository=test_repo)

    class TestProvider:
        def resolve(
            self, principal: Principal, database_id: str | None
        ) -> tuple[TenantDatabaseConfig, SqlQueryService]:
            return (
                TenantDatabaseConfig(principal.org_id, database_id, "sqlite+aiosqlite:///:memory:"),
                test_service,
            )

    monkeypatch.setattr(sql_query_controller, "_tenant_service_provider", TestProvider())
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def mock_valid_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authenticate every request using a validated Auth0 principal."""

    def fake_validate(token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        if token == "user-b":  # noqa: S105 - test-only bearer token
            return {
                "sub": "auth0|user-b",
                "email": "bob@example.com",
                "https://app.secure-db-access-gateway.org/tenant_id": "org-42",
                "roles": ["viewer"],
            }
        if token != "test-valid-token":  # noqa: S105 - test-only bearer token comparison
            return None
        return {
            "sub": "auth0|user-123",
            "email": "alice@example.com",
            "https://app.secure-db-access-gateway.org/tenant_id": "org-42",
            "roles": ["admin"],
        }

    monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", fake_validate)


def auth_headers(token: str = "test-valid-token", **extra: str) -> dict[str, str]:  # noqa: S107
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(extra)
    return headers


class TestGraphQLAliasBombRejected:
    def test_many_aliases_are_rejected(self, client: TestClient) -> None:
        aliases = " ".join(f"a{i}: ping" for i in range(101))
        response = client.post(
            "/graphql",
            json={"query": f"query {{ {aliases} }}"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        assert any("aliases" in err.get("message", "").lower() for err in data["errors"])

    def test_few_aliases_are_allowed(self, client: TestClient) -> None:
        aliases = " ".join(f"a{i}: ping" for i in range(3))
        response = client.post(
            "/graphql",
            json={"query": f"query {{ {aliases} }}"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert "errors" not in data
        assert data["data"]["a0"] == "GraphQL SQL Query API is running!"


class TestGraphQLIntrospectionDisabledInProduction:
    def test_schema_introspection_blocked_when_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        # Importing the app in production requires the Auth0 + policy secrets.
        monkeypatch.setenv("AUTH0_DOMAIN", "test-auth0.example.com")
        monkeypatch.setenv("AUTH0_AUDIENCE", "https://api.example.com")
        monkeypatch.setenv("POLICY_POLICIES_JSON", "[]")
        app = create_app()
        client = TestClient(app)
        response = client.post(
            "/graphql",
            json={"query": "query { __schema { queryType { name } } }"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        assert any("introspection" in err.get("message", "").lower() for err in data["errors"])

    def test_schema_introspection_allowed_in_dev(self, client: TestClient) -> None:
        response = client.post(
            "/graphql",
            json={"query": "query { __schema { queryType { name } } }"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert "errors" not in data
        assert data["data"]["__schema"]["queryType"]["name"] == "Query"


class TestGraphQLBodySizeLimit:
    def test_oversize_body_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRAPHQL_MAX_BODY_BYTES", "200")
        client = TestClient(create_app())
        body = '{"query":"query { ping }","padding":"' + "x" * 2048 + '"}'
        response = client.post(
            "/graphql",
            content=body,
            headers={"Content-Type": "application/json", **auth_headers()},
        )
        assert response.status_code == 413
        assert response.json()["detail"] == "Request body exceeds the maximum allowed size."

    def test_large_but_bounded_body_still_works(self, client: TestClient) -> None:
        response = client.post(
            "/graphql",
            json={"query": "query { ping }"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        assert response.json()["data"]["ping"].startswith("GraphQL")


class TestRateLimitPerPrincipal:
    def test_two_users_get_independent_budgets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
        client = TestClient(create_app())
        payload = {"query": "query { ping }"}

        first_a = client.post("/graphql", json=payload, headers=auth_headers())
        first_b = client.post(
            "/graphql", json=payload, headers=auth_headers(token="user-b")  # noqa: S106
        )
        assert first_a.status_code == 200
        assert first_b.status_code == 200

        second_a = client.post("/graphql", json=payload, headers=auth_headers("user-b"))
        assert second_a.status_code == 429

    def test_single_user_is_budgeted_across_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
        client = TestClient(create_app())
        payload = {"query": "query { ping }"}

        assert client.post("/graphql", json=payload, headers=auth_headers()).status_code == 200
        assert client.post("/graphql", json=payload, headers=auth_headers()).status_code == 429


class TestRateLimitKeySelection:
    def _request_with(self, xff: str | None = None) -> Request:
        headers: list[tuple[bytes, bytes]] = [(b"x-forwarded-for", xff.encode())] if xff else []
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/graphql",
            "raw_path": b"/graphql",
            "query_string": b"",
            "headers": headers,
            "client": ("192.168.1.50", 1234),
            "server": ("internal", 8002),
        }
        return Request(scope)

    def test_principal_is_used_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRUST_PROXY", raising=False)
        middleware = RateLimitMiddleware(app=None)  # type: ignore[arg-type]
        request = self._request_with(xff="9.9.9.9")
        request.state.principal = Principal(
            user_id="auth0|u-7", email="u@example.com", org_id="org-1", roles=frozenset({"admin"})
        )
        assert middleware._get_rate_limit_key(request) == "user:auth0|u-7"

    def test_spoofed_xff_is_ignored_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRUST_PROXY", raising=False)
        middleware = RateLimitMiddleware(app=None)  # type: ignore[arg-type]
        request = self._request_with(xff="9.9.9.9")
        assert middleware._get_rate_limit_key(request) == "192.168.1.50"

    def test_xff_is_honored_when_trust_proxy_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRUST_PROXY", "1")
        middleware = RateLimitMiddleware(app=None)  # type: ignore[arg-type]
        request = self._request_with(xff="9.9.9.9")
        assert middleware._get_rate_limit_key(request) == "9.9.9.9"


class TestRowLimitControls:
    def test_ensure_limit_applies_default_cap(self) -> None:
        repo = SqlQueryRepository(engine=None, sql_safety_checker=None)  # type: ignore[arg-type]
        sql = repo._ensure_limit("SELECT * FROM artist ORDER BY id")
        assert sql.endswith("LIMIT 100")
        assert "LIMIT 100" in sql

    def test_ensure_limit_clamps_runaway_limit(self) -> None:
        repo = SqlQueryRepository(engine=None, sql_safety_checker=None)  # type: ignore[arg-type]
        sql = repo._ensure_limit("SELECT * FROM artist LIMIT 100000000")
        assert sql.upper().endswith("LIMIT 5000")

    def test_ensure_limit_keeps_small_limit(self) -> None:
        repo = SqlQueryRepository(engine=None, sql_safety_checker=None)  # type: ignore[arg-type]
        assert repo._ensure_limit("SELECT * FROM artist LIMIT 10") == "SELECT * FROM artist LIMIT 10"

    def test_ensure_limit_bounds_fetch_first(self) -> None:
        repo = SqlQueryRepository(engine=None, sql_safety_checker=None)  # type: ignore[arg-type]
        sql = repo._ensure_limit("SELECT * FROM artist FETCH FIRST 999999 ROWS ONLY")
        assert "LIMIT 5000" in sql

    def test_fetch_first_small_value_is_kept(self) -> None:
        repo = SqlQueryRepository(engine=None, sql_safety_checker=None)  # type: ignore[arg-type]
        sql = repo._ensure_limit("SELECT * FROM artist FETCH FIRST 3 ROWS ONLY")
        assert "LIMIT 3" in sql

    async def test_explicit_limit_within_budget_returns_exact_rows(
        self, test_engine: AsyncEngine
    ) -> None:
        repo = SqlQueryRepository(engine=test_engine, sql_safety_checker=DefaultSqlSafetyChecker())
        rows = await repo.execute_sql_statement("SELECT id FROM track LIMIT 10")
        assert len(rows) == 10

    async def test_no_explicit_limit_applies_default_100(
        self, test_engine: AsyncEngine
    ) -> None:
        repo = SqlQueryRepository(engine=test_engine, sql_safety_checker=DefaultSqlSafetyChecker())
        rows = await repo.execute_sql_statement("SELECT id FROM track")
        assert len(rows) == 100

    async def test_fetch_first_honored_as_cap(self, test_engine: AsyncEngine) -> None:
        repo = SqlQueryRepository(engine=test_engine, sql_safety_checker=DefaultSqlSafetyChecker())
        rows = await repo.execute_sql_statement("SELECT id, title FROM track FETCH FIRST 3 ROWS ONLY")
        assert [row["id"] for row in rows] == [1, 2, 3]

    async def test_huge_limit_never_fetches_more_than_max(
        self, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SQL_QUERY_MAX_ROW_LIMIT", "10")
        repo = SqlQueryRepository(
            engine=test_engine, sql_safety_checker=DefaultSqlSafetyChecker()
        )
        rows = await repo.execute_sql_statement("SELECT id FROM track LIMIT 100000")
        assert len(rows) == 10
