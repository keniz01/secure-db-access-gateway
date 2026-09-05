"""
Tests that the SQL validator and query executor work with
arbitrary table/column names — no music-domain assumptions.

Uses a completely different 'products/orders/customers' schema.
"""
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
from services.tenant_database_resolver import TenantDatabaseConfig


# ---------------------------------------------------------------------------
# Fixture: an in-memory SQLite DB with a products/orders/customers schema
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
async def products_engine() -> AsyncGenerator[AsyncEngine, None]:
    """In-memory DB with a commerce schema (no music tables at all)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE customers ("
            "  customer_id INTEGER PRIMARY KEY,"
            "  full_name   TEXT NOT NULL,"
            "  email       TEXT NOT NULL UNIQUE"
            ")"
        ))
        await conn.execute(text(
            "CREATE TABLE products ("
            "  product_id  INTEGER PRIMARY KEY,"
            "  sku         TEXT NOT NULL,"
            "  price       REAL NOT NULL"
            ")"
        ))
        await conn.execute(text(
            "CREATE TABLE orders ("
            "  order_id    INTEGER PRIMARY KEY,"
            "  customer_id INTEGER NOT NULL,"
            "  product_id  INTEGER NOT NULL,"
            "  quantity    INTEGER NOT NULL,"
            "  FOREIGN KEY (customer_id) REFERENCES customers(customer_id),"
            "  FOREIGN KEY (product_id)  REFERENCES products(product_id)"
            ")"
        ))
        # Seed
        await conn.execute(text(
            "INSERT INTO customers VALUES "
            "(1, 'Alice Brown', 'alice@example.com'),"
            "(2, 'Bob Smith',  'bob@example.com')"
        ))
        await conn.execute(text(
            "INSERT INTO products VALUES "
            "(1, 'SKU-001', 9.99),"
            "(2, 'SKU-002', 49.99)"
        ))
        await conn.execute(text(
            "INSERT INTO orders VALUES "
            "(1, 1, 1, 3),"
            "(2, 1, 2, 1),"
            "(3, 2, 1, 5)"
        ))

    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
def override_service_with_products_db(
    products_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swap the live service for one backed by the products engine."""
    checker = DefaultSqlSafetyChecker()
    repo = SqlQueryRepository(engine=products_engine, sql_safety_checker=checker)
    service = SqlQueryService(repository=repo)
    class TestProvider:
        def resolve(self, principal, database_id):
            return (
                TenantDatabaseConfig(principal.org_id, database_id, "sqlite+aiosqlite:///:memory:"),
                service,
            )
    monkeypatch.setattr(sql_query_controller, "_tenant_service_provider", TestProvider())


@pytest.fixture(autouse=True)
def mock_valid_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a valid Auth0 principal for GraphQL requests during tests."""
    def fake_validate(token: str | None):
        if token != "test-valid-token":
            return None
        return {
            "sub": "auth0|user-123",
            "email": "alice@example.com",
            "https://app.secure-db-access-gateway.org/tenant_id": "org-42",
            "roles": ["admin"],
        }

    monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", fake_validate)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def auth_headers(**extra):
    headers = {"Authorization": "Bearer test-valid-token"}
    headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestArbitraryTableQueries:
    """Confirm queries run against tables the system has never seen before."""

    def test_select_all_customers(self, client: TestClient) -> None:
        gql = """
        query ExecSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT customer_id, full_name, email FROM customers ORDER BY customer_id"}}
        res = client.post("/graphql", json={"query": gql, "variables": variables}, headers=auth_headers()).json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        assert len(rows) == 2
        assert rows[0]["full_name"] == "Alice Brown"
        assert rows[1]["email"] == "bob@example.com"

    def test_select_products_with_filter(self, client: TestClient) -> None:
        gql = """
        query ExecSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT sku, price FROM products WHERE price > 10.0"}}
        res = client.post("/graphql", json={"query": gql, "variables": variables}, headers=auth_headers()).json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        assert len(rows) == 1
        assert rows[0]["sku"] == "SKU-002"

    def test_join_orders_with_customers(self, client: TestClient) -> None:
        gql = """
        query ExecSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        sql = (
            "SELECT c.full_name, o.quantity "
            "FROM orders o "
            "JOIN customers c ON o.customer_id = c.customer_id "
            "ORDER BY o.order_id"
        )
        variables = {"req": {"sqlStatement": sql}}
        res = client.post("/graphql", json={"query": gql, "variables": variables}, headers=auth_headers()).json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        assert len(rows) == 3
        assert rows[0]["full_name"] == "Alice Brown"
        assert rows[2]["full_name"] == "Bob Smith"

    def test_aggregate_on_arbitrary_table(self, client: TestClient) -> None:
        gql = """
        query ExecSql($req: SqlStatementRequest!) {
            executeSqlStatement(request: $req)
        }
        """
        variables = {"req": {"sqlStatement": "SELECT customer_id, SUM(quantity) FROM orders GROUP BY customer_id ORDER BY customer_id"}}
        res = client.post("/graphql", json={"query": gql, "variables": variables}, headers=auth_headers()).json()
        assert "errors" not in res
        rows = res["data"]["executeSqlStatement"]
        assert len(rows) == 2
        # Alice has orders of qty 3 + 1 = 4
        assert rows[0]["SUM(quantity)"] == 4
        # Bob has order of qty 5
        assert rows[1]["SUM(quantity)"] == 5


class TestArbitrarySchemaIntrospection:
    """Confirm introspectSchema reports the commerce tables, not any fixed set."""

    def test_introspect_returns_commerce_tables(self, client: TestClient) -> None:
        gql = """
        query {
            introspectSchema {
                tables { name columns { name isPrimary } foreignKeys { column foreignTable } }
            }
        }
        """
        res = client.post("/graphql", json={"query": gql}, headers=auth_headers()).json()
        assert "errors" not in res
        tables = {t["name"]: t for t in res["data"]["introspectSchema"]["tables"]}
        assert "customers" in tables
        assert "products" in tables
        assert "orders" in tables

    def test_introspect_orders_foreign_keys(self, client: TestClient) -> None:
        gql = """
        query {
            introspectSchema {
                tables { name foreignKeys { column foreignTable foreignColumn } }
            }
        }
        """
        res = client.post("/graphql", json={"query": gql}, headers=auth_headers()).json()
        assert "errors" not in res
        tables = {t["name"]: t for t in res["data"]["introspectSchema"]["tables"]}
        orders = tables["orders"]
        fk_targets = {fk["foreignTable"] for fk in orders["foreignKeys"]}
        assert "customers" in fk_targets
        assert "products" in fk_targets


class TestValidatorTableAgnostic:
    """Unit-level: verify the safety checker accepts any table name."""

    def test_accepts_any_table_name(self) -> None:
        checker = DefaultSqlSafetyChecker()
        arbitrary_tables = [
            "SELECT * FROM customers",
            "SELECT id FROM xyz123_data_table WHERE active = 1",
            "SELECT a.col1, b.col2 FROM table_a a JOIN table_b b ON a.id = b.ref_id",
            "SELECT COUNT(*) FROM shipments",
        ]
        for sql in arbitrary_tables:
            assert checker.is_safe_select_query(sql) is True, f"Should allow: {sql}"

    def test_rejects_dangerous_regardless_of_table(self) -> None:
        checker = DefaultSqlSafetyChecker()
        dangerous = [
            "DELETE FROM customers WHERE 1=1",
            "DROP TABLE products",
            "INSERT INTO orders (customer_id) VALUES (1)",
        ]
        for sql in dangerous:
            assert checker.is_safe_select_query(sql) is False, f"Should reject: {sql}"
