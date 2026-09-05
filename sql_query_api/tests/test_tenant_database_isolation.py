"""Tenant/database resolution and request scoping tests."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app_factory import create_app
from auth import Principal
from dependencies.tenant_service_provider import TenantServiceProvider
from routes import sql_query_controller
from services.tenant_database_resolver import (
    TenantDatabaseConfig,
    TenantDatabaseResolutionError,
    TenantDatabaseResolver,
)


TEST_DB_A = Path(__file__).with_name("tenant_org_a.sqlite")
TEST_DB_B = Path(__file__).with_name("tenant_org_b.sqlite")


async def _create_database(path: Path, table: str, value: str) -> AsyncEngine:
    if path.exists():
        path.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)"))
        await connection.execute(text(f"INSERT INTO {table} (id, value) VALUES (1, :value)"), {"value": value})
    await engine.dispose()
    return engine


@pytest.fixture
async def tenant_databases() -> AsyncGenerator[dict[str, str], None]:
    await _create_database(TEST_DB_A, "org_a_data", "org-a-secret")
    await _create_database(TEST_DB_B, "org_b_data", "org-b-secret")
    yield {
        "org-a": f"sqlite+aiosqlite:///{TEST_DB_A}",
        "org-b": f"sqlite+aiosqlite:///{TEST_DB_B}",
    }
    for path in (TEST_DB_A, TEST_DB_B):
        if path.exists():
            path.unlink()


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer org-a-token"}


def _query(database_id: str, sql: str) -> dict:
    return {
        "query": """
            query Execute($request: SqlStatementRequest!) {
                executeSqlStatement(request: $request)
            }
        """,
        "variables": {"request": {"databaseId": database_id, "sqlStatement": sql}},
    }


def test_resolver_fails_closed_for_unknown_cross_tenant_and_ambiguous_bindings() -> None:
    resolver = TenantDatabaseResolver(
        [
            TenantDatabaseConfig("org-a", "db-a", "sqlite+aiosqlite:///a"),
            TenantDatabaseConfig("org-b", "db-b", "sqlite+aiosqlite:///b"),
            TenantDatabaseConfig("org-a", "ambiguous", "sqlite+aiosqlite:///a"),
            TenantDatabaseConfig("org-a", "ambiguous", "sqlite+aiosqlite:///other"),
        ]
    )
    principal_a = Principal("user-a", "a@example.com", "org-a", frozenset({"viewer"}))

    assert resolver.resolve(principal_a, "db-a").connection_string == "sqlite+aiosqlite:///a"
    with pytest.raises(TenantDatabaseResolutionError):
        resolver.resolve(principal_a, "db-b")
    with pytest.raises(TenantDatabaseResolutionError):
        resolver.resolve(principal_a, "missing")
    with pytest.raises(TenantDatabaseResolutionError):
        resolver.resolve(principal_a, "ambiguous")
    with pytest.raises(TenantDatabaseResolutionError):
        resolver.resolve(principal_a, "sqlite:///client-supplied")


def test_production_configuration_never_uses_legacy_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///legacy")
    monkeypatch.delenv("TENANT_DATABASES_JSON", raising=False)
    monkeypatch.delenv("TENANT_DATABASES_FILE", raising=False)

    with pytest.raises(RuntimeError, match="Tenant database configuration is required"):
        TenantDatabaseResolver.from_environment()


@pytest.fixture
def tenant_client(
    tenant_databases: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    resolver = TenantDatabaseResolver(
        [
            TenantDatabaseConfig("org-a", "db-a", tenant_databases["org-a"]),
            TenantDatabaseConfig("org-b", "db-b", tenant_databases["org-b"]),
        ]
    )
    monkeypatch.setattr(sql_query_controller, "_tenant_database_resolver", resolver)
    monkeypatch.setattr(sql_query_controller, "_tenant_service_provider", TenantServiceProvider(resolver))
    monkeypatch.setattr(sql_query_controller, "_sql_query_service", None)

    def validate(token: str | None):
        return {
            "sub": "user-a",
            "email": "a@example.com",
            "https://app.secure-db-access-gateway.org/tenant_id":
                "org-a" if token == "org-a-token" else "org-b",
            "roles": ["viewer"],
        } if token in {"org-a-token", "org-b-token"} else None

    monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", validate)
    return TestClient(create_app())


def test_query_is_bound_to_logical_database_and_trusted_org(
    tenant_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        "routes.sql_query_controller.log_audit_event",
        lambda event_type, **payload: captured.update(event=event_type, **payload),
    )

    response = tenant_client.post(
        "/graphql",
        json=_query("db-a", "SELECT value FROM org_a_data"),
        headers=_headers(),
    )
    assert response.json()["data"]["executeSqlStatement"] == [{"value": "org-a-secret"}]
    assert captured["org_id"] == "org-a"
    assert captured["database_id"] == "db-a"

    cross_tenant = tenant_client.post(
        "/graphql",
        json=_query("db-b", "SELECT value FROM org_b_data"),
        headers=_headers(),
    )
    assert "not available for this organisation" in cross_tenant.json()["errors"][0]["message"]


def test_headers_and_query_parameters_cannot_select_database(
    tenant_client: TestClient,
) -> None:
    response = tenant_client.post(
        "/graphql?database_id=db-b",
        json=_query("db-a", "SELECT value FROM org_a_data"),
        headers={**_headers(), "X-Database-Id": "db-b", "X-Org-Id": "org-b"},
    )
    assert response.json()["data"]["executeSqlStatement"] == [{"value": "org-a-secret"}]


def test_schema_introspection_uses_the_same_resolved_database(
    tenant_client: TestClient,
) -> None:
    response = tenant_client.post(
        "/graphql",
        json={
            "query": """
                query Introspect($databaseId: String!) {
                    introspectSchema(databaseId: $databaseId) {
                        tables { name }
                    }
                }
            """,
            "variables": {"databaseId": "db-a"},
        },
        headers=_headers(),
    )
    table_names = {table["name"] for table in response.json()["data"]["introspectSchema"]["tables"]}
    assert table_names == {"org_a_data"}


def test_embedding_lookup_receives_trusted_tenant_context(
    tenant_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeService:
        async def get_table_schema(self, embeddings):
            return {"schema": "org-a schema"}

    class FakeProvider:
        def resolve(self, principal, database_id):
            calls.append((principal.org_id, database_id))
            return (
                TenantDatabaseConfig("org-a", database_id, "sqlite+aiosqlite:///server-owned"),
                FakeService(),
            )

    monkeypatch.setattr(sql_query_controller, "_tenant_service_provider", FakeProvider())
    response = tenant_client.post(
        "/graphql",
        json={
            "query": """
                query Schema($databaseId: String!, $embeddings: [Float!]!) {
                    getTableSchema(databaseId: $databaseId, embeddings: $embeddings) { schema }
                }
            """,
            "variables": {"databaseId": "db-a", "embeddings": [0.0] * 768},
        },
        headers=_headers(),
    )
    assert response.json()["data"]["getTableSchema"]["schema"] == "org-a schema"
    assert calls == [("org-a", "db-a")]
