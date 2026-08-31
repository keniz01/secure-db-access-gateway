import json
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

from app_factory import create_app
from routes import sql_query_controller
from repositories.sql_query_repository import SqlQueryRepository
from services.sql_query_service import SqlQueryService
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker


@pytest.fixture(scope="module")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an in-memory SQLite async engine and seed test tables."""
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
            await conn.execute(text(f"INSERT INTO track (id, title) VALUES ({i}, 'Track {i}')"))

    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
def override_sql_service(test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the SQL query service instance in sql_query_controller with test database engine."""
    safety_checker = DefaultSqlSafetyChecker()
    test_repo = SqlQueryRepository(engine=test_engine, sql_safety_checker=safety_checker)
    test_service = SqlQueryService(repository=test_repo)
    monkeypatch.setattr(sql_query_controller, "_sql_query_service", test_service)


@pytest.fixture(autouse=True)
def mock_valid_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a valid Auth0 principal for requests that include a bearer token."""
    def fake_validate(token: str | None):
        if token != "test-valid-token":
            return None
        return {
            "sub": "auth0|user-123",
            "email": "alice@example.com",
            "org_id": "org-42",
            "roles": ["admin"],
        }

    monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", fake_validate)


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def auth_headers(**extra):
    headers = {"Authorization": "Bearer test-valid-token"}
    headers.update(extra)
    return headers


class TestGraphQLHealthCheck:
    def test_graphql_requires_valid_bearer_token(self, client: TestClient) -> None:
        payload = {"query": "query { ping }"}
        response = client.post("/graphql", json=payload)
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required."

    def test_graphql_rejects_token_without_an_organisation_claim(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "middlewares.rbac_middleware.validate_access_token",
            lambda _: {"sub": "auth0|user-123", "email": "alice@example.com", "roles": ["viewer"]},
        )

        response = client.post("/graphql", json={"query": "query { ping }"}, headers=auth_headers())

        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required."

    def test_graphql_ping(self, client: TestClient) -> None:
        payload = {"query": "query { ping }"}
        response = client.post("/graphql", json=payload, headers=auth_headers())
        assert response.status_code == 200
        json_data = response.json()
        assert "errors" not in json_data
        assert json_data["data"]["ping"] == "GraphQL SQL Query API is running!"


class TestGraphQLExecuteSqlStatement:
    def test_execute_sql_success(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {
            "req": {
                "sqlStatement": "SELECT id, name, genre FROM artist ORDER BY id ASC"
            }
        }
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        assert len(rows) == 2
        assert rows[0] == {"id": 1, "name": "The Beatles", "genre": "Rock"}
        assert rows[1] == {"id": 2, "name": "Miles Davis", "genre": "Jazz"}

    def test_graphql_rate_limit_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
        app = create_app()
        client = TestClient(app)
        payload = {"query": "query { ping }"}

        first = client.post("/graphql", json=payload, headers=auth_headers())
        second = client.post("/graphql", json=payload, headers=auth_headers())

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()["detail"] == "Rate limit exceeded. Please try again later."
        assert second.headers["Retry-After"] == "60"

    def test_execute_sql_automatic_limit(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT id, title FROM track"}}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        # Default LIMIT 100 should be applied automatically
        assert len(rows) == 100

    def test_graphql_forged_role_header_is_ignored(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT id, name FROM artist ORDER BY id ASC"}}
        response = client.post(
            "/graphql",
            json={"query": gql_query, "variables": variables},
            headers=auth_headers(**{"X-User-Role": "guest"}),
        )
        assert response.status_code == 200
        assert response.json()["data"]["executeSqlStatement"][0]["name"] == "The Beatles"

    def test_execute_sql_emits_audit_json(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT id, name FROM artist ORDER BY id ASC"}}
        headers = auth_headers(**{"X-User-Email": "evil@example.com", "X-Org-Id": "evil-org"})

        captured = {}

        def fake_log(event_type: str, **payload):
            captured["event"] = event_type
            captured.update(payload)

        monkeypatch.setattr("routes.sql_query_controller.log_audit_event", fake_log)

        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=headers)
        assert response.status_code == 200
        assert captured["event"] == "sql_query"
        assert captured["user"] == "alice@example.com"
        assert captured["org_id"] == "org-42"
        assert captured["tables_touched"] == ["artist"]

    def test_execute_sql_empty_statement(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "   "}}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" in res
        assert "SQL statement cannot be empty." in res["errors"][0]["message"]

    def test_execute_sql_too_long_statement(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        long_sql = "SELECT * FROM artist WHERE name = '" + "A" * 10005 + "'"
        variables = {"req": {"sqlStatement": long_sql}}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" in res
        assert "SQL statement is too long" in res["errors"][0]["message"]

    def test_execute_sql_rejected_dml(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "INSERT INTO artist (id, name) VALUES (3, 'Hacker')"}}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" in res
        assert "SELECT" in res["errors"][0]["message"]

    def test_execute_sql_syntax_error(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT FROM non_existent_table"}}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" in res
        assert "Failed to execute SQL statement. Please verify your query syntax." in res["errors"][0]["message"]


class TestGraphQLGetTableSchema:
    def test_get_table_schema_empty_embeddings(self, client: TestClient) -> None:
        gql_query = """
        query GetSchema($emb: [Float!]!) {
            getTableSchema(embeddings: $emb) {
                schema
            }
        }
        """
        variables = {"emb": []}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" in res
        assert "Embeddings list cannot be empty." in res["errors"][0]["message"]

    def test_get_table_schema_invalid_dimensions(self, client: TestClient) -> None:
        gql_query = """
        query GetSchema($emb: [Float!]!) {
            getTableSchema(embeddings: $emb) {
                schema
            }
        }
        """
        variables = {"emb": [0.1, 0.2, 0.3]}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" in res
        assert "Embeddings must be exactly 384 dimensions, got 3" in res["errors"][0]["message"]



class TestGraphQLIntrospectSchema:
    def test_introspect_schema_returns_tables(self, client: TestClient) -> None:
        gql_query = """
        query {
            introspectSchema {
                tables {
                    name
                    schemaName
                    columns {
                        name
                        type
                        nullable
                        isPrimary
                    }
                    foreignKeys {
                        column
                        foreignTable
                        foreignColumn
                    }
                }
            }
        }
        """
        response = client.post("/graphql", json={"query": gql_query}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" not in res
        tables = res["data"]["introspectSchema"]["tables"]
        assert isinstance(tables, list)
        assert len(tables) >= 2
        table_names = [t["name"] for t in tables]
        assert "artist" in table_names
        assert "track" in table_names

    def test_introspect_schema_artist_columns(self, client: TestClient) -> None:
        gql_query = """
        query {
            introspectSchema {
                tables {
                    name
                    columns { name type nullable isPrimary }
                }
            }
        }
        """
        response = client.post("/graphql", json={"query": gql_query}, headers=auth_headers())
        assert response.status_code == 200
        res = response.json()
        assert "errors" not in res
        tables = {t["name"]: t for t in res["data"]["introspectSchema"]["tables"]}
        artist = tables["artist"]
        col_names = [c["name"] for c in artist["columns"]]
        assert "id" in col_names
        assert "name" in col_names
        assert "genre" in col_names


class TestSecurityHeadersMiddleware:
    def test_security_headers_present(self, client: TestClient) -> None:
        response = client.post("/graphql", json={"query": "query { ping }"}, headers=auth_headers())
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-XSS-Protection") == "1; mode=block"
        assert "max-age=" in response.headers.get("Strict-Transport-Security", "")
