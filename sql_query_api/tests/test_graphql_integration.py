import pytest
from typing import AsyncGenerator
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


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


class TestGraphQLHealthCheck:
    def test_graphql_ping(self, client: TestClient) -> None:
        payload = {"query": "query { ping }"}
        response = client.post("/graphql", json=payload)
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
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
        assert response.status_code == 200
        res = response.json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        assert len(rows) == 2
        assert rows[0] == {"id": 1, "name": "The Beatles", "genre": "Rock"}
        assert rows[1] == {"id": 2, "name": "Miles Davis", "genre": "Jazz"}

    def test_execute_sql_automatic_limit(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT id, title FROM track"}}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
        assert response.status_code == 200
        res = response.json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        # Default LIMIT 100 should be applied automatically
        assert len(rows) == 100

    def test_execute_sql_empty_statement(self, client: TestClient) -> None:
        gql_query = """
        query ExecuteSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "   "}}
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
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
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
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
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
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
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
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
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
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
        response = client.post("/graphql", json={"query": gql_query, "variables": variables})
        assert response.status_code == 200
        res = response.json()
        assert "errors" in res
        assert "Embeddings must be exactly 384 dimensions, got 3" in res["errors"][0]["message"]


class TestSecurityHeadersMiddleware:
    def test_security_headers_present(self, client: TestClient) -> None:
        response = client.post("/graphql", json={"query": "query { ping }"})
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-XSS-Protection") == "1; mode=block"
        assert "max-age=" in response.headers.get("Strict-Transport-Security", "")
