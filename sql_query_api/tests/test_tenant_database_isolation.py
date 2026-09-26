"""Tenant/database resolution and request scoping tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app_factory import create_app
from auth import Principal
from dependencies.tenant_service_provider import TenantServiceProvider
from routes import sql_query_controller
from services.policy_engine import PolicyEvaluator
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
        await connection.execute(
            text(  # noqa: S608 - table name comes from a fixed test constant
                f"INSERT INTO {table} (id, value) VALUES (1, :value)"  # noqa: S608 - not user input
            ),
            {"value": value},
        )
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
    monkeypatch.delenv("TENANT_DATABASES_CONFIG_FILE", raising=False)

    with pytest.raises(RuntimeError, match="Tenant database configuration not found"):
        TenantDatabaseResolver.from_environment()


def test_policy_configuration_is_required_only_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLICY_POLICIES_JSON", raising=False)
    monkeypatch.delenv("POLICY_POLICIES_JSON_FILE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("CI", raising=False)

    assert PolicyEvaluator.from_environment().enabled is False

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="Missing required POLICY_POLICIES_JSON"):
        PolicyEvaluator.from_environment()


def test_policy_configuration_loads_from_secret_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_file = tmp_path / "policy_policies.json"
    policy_file.write_text("[]")
    monkeypatch.delenv("POLICY_POLICIES_JSON", raising=False)
    monkeypatch.setenv("POLICY_POLICIES_JSON_FILE", str(policy_file))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("CI", raising=False)

    evaluator = PolicyEvaluator.from_environment()
    assert evaluator.enabled is True
    assert evaluator.policies == ()


def test_replica_configuration_is_resolved_and_audited() -> None:
    config = TenantDatabaseConfig(
        org_id="org-a",
        database_id="db-a",
        connection_string="sqlite+aiosqlite:///primary.db",
        replica_connection_string="sqlite+aiosqlite:///replica.db",
    )
    assert config.effective_connection_string == "sqlite+aiosqlite:///replica.db"
    assert config.effective_target == "replica"

    config_with_primary = TenantDatabaseConfig(
        org_id="org-a",
        database_id="db-a",
        connection_string="sqlite+aiosqlite:///primary.db",
        use_read_replica=False,
    )
    assert config_with_primary.effective_target == "primary"


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

    def validate(token: str | None) -> dict[str, Any] | None:
        return {
            "sub": "user-a",
            "email": "a@example.com",
            "https://app.secure-db-access-gateway.org/tenant_id":
                "org-a" if token == "org-a-token" else "org-b",  # noqa: S105 - test-only bearer tokens
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
        async def get_table_schema(self, embeddings: list[float]) -> dict[str, str]:
            return {"schema": "org-a schema"}

    class FakeProvider:
        def resolve(
            self, principal: Principal, database_id: str | None
        ) -> tuple[TenantDatabaseConfig, FakeService]:
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
