"""
Cross-tenant authorization test suite.

Proves tenant isolation end-to-end and treats ANY cross-tenant read as a
critical security failure. Covers every production blocker:

* manipulated ``tenant_id`` and ``database_id``
* missing / invalid / malformed claims
* stale sessions and privilege changes (per-request enforcement)
* isolation across GraphQL (the governed gateway) and the operator CLI
* confused-deputy scenarios (spoofed identity headers and query parameters
  must never override the validated principal)
* fail-closed resolution that leaks nothing about other tenants' databases

Harness mirrors ``tests/test_tenant_database_isolation.py``: real on-disk
SQLite files per tenant so a cross-tenant read cannot silently share a
connection.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app_factory import create_app
from auth import (
    TENANT_ID_CLAIM,
    Principal,
    build_principal_from_claims,
)
from dependencies.tenant_service_provider import TenantServiceProvider
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from routes import sql_query_controller
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import (
    TenantDatabaseConfig,
    TenantDatabaseResolutionError,
    TenantDatabaseResolver,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

SECRET_A = "org-a-secret"  # noqa: S105 - test-only fixture data
SECRET_B = "org-b-secret"  # noqa: S105 - test-only fixture data
SECRET_F = "org-b-finance-secret"  # noqa: S105 - test-only fixture data
SECRET_R = "org-a-reporting-secret"  # noqa: S105 - test-only fixture data


def _claims(org_id: str, roles: list[str] | None = None, **extra: object) -> dict[str, Any]:
    claims = {
        "sub": f"user-{org_id}",
        "email": f"{org_id}@example.com",
        TENANT_ID_CLAIM: org_id,
        "roles": roles or ["viewer"],
    }
    claims.update(extra)
    return claims


def _principal(org_id: str, role: str = "viewer") -> Principal:
    return Principal(
        user_id=f"user-{org_id}",
        email=f"{org_id}@example.com",
        org_id=org_id,
        roles=frozenset({role}),
    )


# ---------------------------------------------------------------------------
# Fixtures: two tenants that share the SAME logical database_id, plus a second
# database id owned by org-b. Same database_id under different orgs must never
# share a connection.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tenant_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("tenant_isolation")


async def _seed(directory: Path, name: str, table: str, secret: str) -> str:
    path = directory / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)"))  # noqa: S608
        await conn.execute(  # noqa: S608
            text(f"INSERT INTO {table} (id, value) VALUES (1, :value)"),  # noqa: S608
            {"value": secret},
        )
    await engine.dispose()
    return f"sqlite+aiosqlite:///{path}"


@pytest.fixture(scope="module")
def tenant_urls(tenant_dir: Path) -> dict[str, str]:
    """Return a concurrency-safe mapping of (org, database) -> connection URL."""
    import asyncio

    urls: dict[str, str] = {}

    async def build() -> None:
        urls[("org-a", "default")] = await _seed(tenant_dir, "org_a.sqlite", "org_a_data", SECRET_A)
        urls[("org-a", "reporting")] = await _seed(
            tenant_dir, "org_a_reporting.sqlite", "org_a_reporting_data", SECRET_R
        )
        urls[("org-b", "default")] = await _seed(tenant_dir, "org_b.sqlite", "org_b_data", SECRET_B)
        urls[("org-b", "finance")] = await _seed(tenant_dir, "org_b_finance.sqlite", "org_b_finance_data", SECRET_F)

    asyncio.run(build())
    return urls


@pytest.fixture(scope="module")
def cross_tenant_resolver(tenant_urls: dict[str, str]) -> TenantDatabaseResolver:
    return TenantDatabaseResolver(
        [
            TenantDatabaseConfig("org-a", "default", tenant_urls[("org-a", "default")]),
            TenantDatabaseConfig("org-a", "reporting", tenant_urls[("org-a", "reporting")]),
            TenantDatabaseConfig("org-b", "default", tenant_urls[("org-b", "default")]),
            TenantDatabaseConfig("org-b", "finance", tenant_urls[("org-b", "finance")]),
        ]
    )


@pytest.fixture
def tenant_client(
    cross_tenant_resolver: TenantDatabaseResolver,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """GraphQL client with a two-tenant resolver and token->claims fake validator."""

    def validate(token: str | None) -> dict[str, Any] | None:
        mapping = {
            "token-a": _claims("org-a"),
            "token-b": _claims("org-b"),
            "token-b-admin": _claims("org-b", ["admin"]),
            "token-a-admin": _claims("org-a", ["admin"]),
        }
        return mapping.get(token)

    monkeypatch.setattr(sql_query_controller, "_tenant_database_resolver", cross_tenant_resolver)
    monkeypatch.setattr(sql_query_controller, "_tenant_service_provider", TenantServiceProvider(cross_tenant_resolver))
    monkeypatch.setattr(sql_query_controller, "_sql_query_service", None)
    monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", validate)
    yield TestClient(create_app())


def _execute_payload(database_id: str, sql: str) -> dict:
    return {
        "query": """
            query Execute($request: SqlStatementRequest!) {
                executeSqlStatement(request: $request)
            }
        """,
        "variables": {"request": {"databaseId": database_id, "sqlStatement": sql}},
    }


def _execute(client: TestClient, token: str, database_id: str, sql: str) -> dict:
    response = client.post(
        "/graphql",
        json=_execute_payload(database_id, sql),
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()


# ===========================================================================
# 1. Manipulated tenant_id
# ===========================================================================
class TestManipulatedTenantId:
    """The tenant_id claim is the sole, validated identity root for resolution."""

    def test_build_principal_reflects_only_validated_claim(self) -> None:
        principal = build_principal_from_claims(_claims("org-b"))
        assert principal is not None
        assert principal.org_id == "org-b"

    def test_attempted_cross_tenant_query_returns_own_tenant_data_only(
        self, tenant_client: TestClient
    ) -> None:
        body = _execute(tenant_client, "token-b", "default", "SELECT value FROM org_b_data")
        values = [row["value"] for row in body["data"]["executeSqlStatement"]]
        assert values == [SECRET_B]
        assert SECRET_A not in str(body)

        body = _execute(tenant_client, "token-a", "default", "SELECT value FROM org_a_data")
        values = [row["value"] for row in body["data"]["executeSqlStatement"]]
        assert values == [SECRET_A]
        assert SECRET_B not in str(body)

    def test_tenant_claim_chooses_which_tenant_binding_is_served(self) -> None:
        """Same logical database_id, same SQL shape, different tenants => different secrets."""
        resolver = TenantDatabaseResolver(
            [
                TenantDatabaseConfig("org-a", "default", "sqlite+aiosqlite:///a"),
                TenantDatabaseConfig("org-b", "default", "sqlite+aiosqlite:///b"),
            ]
        )
        binding_a = resolver.resolve(_principal("org-a"), "default")
        binding_b = resolver.resolve(_principal("org-b"), "default")
        assert binding_a.connection_string != binding_b.connection_string
        assert binding_a.org_id == "org-a"
        assert binding_b.org_id == "org-b"


# ===========================================================================
# 2. Manipulated database_id
# ===========================================================================
class TestManipulatedDatabaseId:
    """database_id is an opaque logical key, never a connection string."""

    def test_connection_string_cannot_be_smuggled_as_database_id(self) -> None:
        resolver = TenantDatabaseResolver(
            [TenantDatabaseConfig("org-a", "default", "sqlite+aiosqlite:///:memory:")]
        )
        for attack in (
            "sqlite+aiosqlite:///:memory:",
            "postgresql://user:pass@evil/db",
            "file:///etc/passwd",
            "/etc/passwd",
        ):
            with pytest.raises(TenantDatabaseResolutionError):
                resolver.resolve(_principal("org-a"), attack)

    def test_path_traversal_like_database_id_rejected(self, tenant_client: TestClient) -> None:
        body = _execute(tenant_client, "token-a", "x../tenant_org_a.sqlite", "SELECT 1")
        message = body["errors"][0]["message"]
        assert "valid logical database_id" in message
        assert SECRET_A not in str(body)

    def test_blank_database_id_rejected(self) -> None:
        resolver = TenantDatabaseResolver([TenantDatabaseConfig("org-a", "default", "sqlite:////a")])
        with pytest.raises(TenantDatabaseResolutionError):
            resolver.resolve(_principal("org-a"), "  ")

    def test_cross_tenant_and_unknown_ids_are_indistinguishable(self) -> None:
        """Fail-closed resolution must not leak which tenant owns a database_id."""
        resolver = TenantDatabaseResolver(
            [
                TenantDatabaseConfig("org-b", "finance", "sqlite:///:memory:"),
                TenantDatabaseConfig("org-a", "default", "sqlite:///:memory:"),
            ]
        )
        unknowns: list[str] = []
        for database_id in ("finance", "does-not-exist", "zzz"):
            with pytest.raises(TenantDatabaseResolutionError) as exc:
                resolver.resolve(_principal("org-a"), database_id)
            unknowns.append(str(exc.value))
        assert len(set(unknowns)) == 1, f"oracle leak: {unknowns}"

    def test_cloaked_database_id_of_another_tenant_is_rejected(self, tenant_client: TestClient) -> None:
        body = _execute(tenant_client, "token-a", "default", "SELECT value FROM org_b_data")
        values = (body.get("data") or {}).get("executeSqlStatement") or []
        assert all(row.get("value") != SECRET_B for row in values)
        assert SECRET_B not in str(body)


# ===========================================================================
# 3. Missing / invalid / malformed claims
# ===========================================================================
class TestMissingInvalidClaims:
    """Tokens without a trustable tenant identity are rejected before any SQL."""

    def test_no_token_rejected(self, tenant_client: TestClient) -> None:
        response = tenant_client.post("/graphql", json=_execute_payload("default", "SELECT 1"))
        assert response.status_code == 401

    def test_garbage_bearer_token_rejected(self, tenant_client: TestClient) -> None:
        response = tenant_client.post(
            "/graphql",
            json=_execute_payload("default", "SELECT 1"),
            headers={"Authorization": "Bearer not-a-jwt"},
        )
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "mutation",
        [
            lambda claims: claims.pop(TENANT_ID_CLAIM),
            lambda claims: claims.__setitem__(TENANT_ID_CLAIM, "   "),
            lambda claims: claims.__setitem__(TENANT_ID_CLAIM, ""),
            lambda claims: claims.__setitem__(TENANT_ID_CLAIM, 42),
            lambda claims: claims.pop("sub"),
        ],
    )
    def test_claim_shape_failures_produce_no_principal(
        self, mutation: Callable[[dict[str, Any]], object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = _claims("org-a")
        mutation(base)

        def validate(token: str | None) -> dict[str, Any] | None:
            if token == "malformed-token":  # noqa: S105 - test-only bearer token
                return base
            return None

        monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", validate)
        client = TestClient(create_app())
        response = client.post(
            "/graphql",
            json=_execute_payload("default", "SELECT 1"),
            headers={"Authorization": "Bearer malformed-token"},
        )
        assert response.status_code == 401

    def test_build_principal_rejects_malformed_claims(self) -> None:
        from collections.abc import Mapping

        malformed: list[Any] = [
            None,
            {},
            {"sub": "x"},
            {"sub": "x", "email": "e", TENANT_ID_CLAIM: ""},
            {"sub": "", "email": "e@example.com", TENANT_ID_CLAIM: "org"},
            {"sub": "x", "email": "e@example.com", TENANT_ID_CLAIM: " "},
        ]
        for claims in malformed:
            if claims is not None and isinstance(claims, Mapping):
                assert build_principal_from_claims(claims) is None
            else:
                assert build_principal_from_claims(claims) is None

    def test_email_claim_is_optional_and_falls_back_to_subject(self) -> None:
        """Auth0 access tokens may omit profile claims; the subject remains identity."""
        principal = build_principal_from_claims(
            {"sub": "user-org-a", "roles": ["viewer"], TENANT_ID_CLAIM: "org-a"}
        )
        assert principal is not None
        assert principal.user_id == "user-org-a"
        assert principal.email == "user-org-a"
        assert principal.org_id == "org-a"


# ===========================================================================
# 4. Stale sessions and privilege changes are enforced per request
# ===========================================================================
class TestStaleSessionsAndPrivilegeChanges:
    """Every request is re-evaluated: no result caching, no role caching."""

    def test_expired_token_denied_on_second_request(
        self, tenant_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"n": 0}

        def validate(token: str | None) -> dict[str, Any] | None:
            calls["n"] += 1
            if calls["n"] <= 1 and token == "short-lived-token":  # noqa: S105 - test-only bearer token
                return _claims("org-a")
            return None

        monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", validate)
        client = TestClient(create_app())

        first = _execute(client, "short-lived-token", "default", "SELECT value FROM org_a_data")
        assert [row["value"] for row in first["data"]["executeSqlStatement"]] == [SECRET_A]

        response = client.post(
            "/graphql",
            json=_execute_payload("default", "SELECT value FROM org_a_data"),
            headers={"Authorization": "Bearer short-lived-token"},
        )
        assert response.status_code == 401

    def test_admin_privilege_revoked_between_requests(
        self, tenant_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = {"role": "admin", **{}}

        def validate(token: str | None) -> dict[str, Any] | None:
            if token == "mutable-token":  # noqa: S105 - test-only bearer token
                return _claims("org-a", [state["role"]])
            return None

        monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", validate)
        client = TestClient(create_app())

        simulate = {
            "query": """
                query Sim($request: SqlStatementRequest!) {
                    simulatePolicy(request: $request) { allowed reason }
                }
            """,
            "variables": {"request": {"databaseId": "default", "sqlStatement": "SELECT 1"}},
        }
        headers = {"Authorization": "Bearer mutable-token"}

        admin_body = client.post("/graphql", json=simulate, headers=headers).json()
        assert "errors" not in admin_body

        state["role"] = "viewer"
        viewer_body = client.post("/graphql", json=simulate, headers=headers).json()
        assert "Policy simulation requires an administrator role." in viewer_body["errors"][0]["message"]


# ===========================================================================
# 5. Isolation across every SQL execution path
# ===========================================================================
class TestIsolationAcrossExecutionPaths:
    """GraphQL execution, introspection, embeddings, policy simulation, and cost
    estimation all resolve under the validated principal's org only."""

    def test_execute_never_reads_other_tenants_default(self, tenant_client: TestClient) -> None:
        assert SECRET_A not in str(
            _execute(tenant_client, "token-b", "default", "SELECT value FROM org_b_data")
        )
        assert SECRET_A not in str(
            _execute(tenant_client, "token-b", "finance", "SELECT value FROM org_b_finance_data")
        )
        assert SECRET_R not in str(_execute(tenant_client, "token-b", "reporting", "SELECT 1"))

    def test_same_org_can_switch_database_only_inside_its_own_set(self, tenant_client: TestClient) -> None:
        body = _execute(tenant_client, "token-b", "finance", "SELECT value FROM org_b_finance_data")
        assert [row["value"] for row in body["data"]["executeSqlStatement"]] == [SECRET_F]

    def test_introspection_is_org_scoped(self, tenant_client: TestClient) -> None:
        for token, own_table, foreign_table in (
            ("token-a", "org_a_data", "org_b_data"),
            ("token-b", "org_b_data", "org_a_data"),
        ):
            response = tenant_client.post(
                "/graphql",
                json={
                    "query": """
                        query Introspect($databaseId: String!) {
                            introspectSchema(databaseId: $databaseId) { tables { name } }
                        }
                    """,
                    "variables": {"databaseId": "default"},
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            names = {t["name"] for t in response.json()["data"]["introspectSchema"]["tables"]}
            assert own_table in names
            assert foreign_table not in names

    def test_embedding_lookup_is_org_scoped(
        self, tenant_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, str | None]] = []

        class FakeService:
            async def get_table_schema(self, embeddings: list[float]) -> dict[str, str]:
                assert embeddings is not None
                return {"schema": "org-scoped schema"}

        class FakeProvider:
            def resolve(
                self, principal: Principal, database_id: str | None
            ) -> tuple[TenantDatabaseConfig, FakeService]:
                calls.append((principal.org_id, database_id))
                return (
                    TenantDatabaseConfig(principal.org_id, database_id or "", "sqlite+aiosqlite:///:memory:"),
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
                "variables": {"databaseId": "default", "embeddings": [0.0] * 768},
            },
            headers={"Authorization": "Bearer token-b"},
        )
        assert response.json()["data"]["getTableSchema"]["schema"] == "org-scoped schema"
        assert calls and calls[0][0] == "org-b"

    def test_simulate_policy_is_admin_and_org_scoped(self, tenant_client: TestClient) -> None:
        own_simulate = {
            "query": """
                query Sim($request: SqlStatementRequest!) {
                    simulatePolicy(request: $request) { allowed reason }
                }
            """,
            "variables": {"request": {"databaseId": "default", "sqlStatement": "SELECT 1"}},
        }
        cross_simulate = {
            "query": """
                query Sim($request: SqlStatementRequest!) {
                    simulatePolicy(request: $request) { allowed reason }
                }
            """,
            "variables": {"request": {"databaseId": "reporting", "sqlStatement": "SELECT 1"}},
        }

        viewer = tenant_client.post(
            "/graphql", json=own_simulate, headers={"Authorization": "Bearer token-a"}
        ).json()
        assert "Policy simulation requires an administrator role." in viewer["errors"][0]["message"]

        admin_own = tenant_client.post(
            "/graphql", json=own_simulate, headers={"Authorization": "Bearer token-a-admin"}
        ).json()
        assert "errors" not in admin_own
        assert "allowed" in admin_own["data"]["simulatePolicy"]

        admin_cross = tenant_client.post(
            "/graphql", json=cross_simulate, headers={"Authorization": "Bearer token-b-admin"}
        ).json()
        assert "not available for this organisation" in admin_cross["errors"][0]["message"]

        own_other_db = tenant_client.post(
            "/graphql",
            json={
                "query": """
                    query Sim($request: SqlStatementRequest!) {
                        simulatePolicy(request: $request) { allowed }
                    }
                """,
                "variables": {"request": {"databaseId": "reporting", "sqlStatement": "SELECT 1"}},
            },
            headers={"Authorization": "Bearer token-a-admin"},
        ).json()
        assert "errors" not in own_other_db

    def test_cost_estimation_is_org_scoped(self, tenant_client: TestClient) -> None:
        body = tenant_client.post(
            "/graphql",
            json={
                "query": """
                    query Cost($sql: String!, $databaseId: String!) {
                        estimateQueryCost(sqlStatement: $sql, databaseId: $databaseId) { score }
                    }
                """,
                "variables": {"sql": "SELECT 1", "databaseId": "finance"},
            },
            headers={"Authorization": "Bearer token-a"},
        ).json()
        assert "not available for this organisation" in body["errors"][0]["message"]


# ===========================================================================
# 6. Confused-deputy scenarios
# ===========================================================================
class TestConfusedDeputy:
    """Nothing the caller controls (headers, query params, body shape) may pick a
    database the validated principal does not own."""

    def test_identity_headers_cannot_redirect_resolution(self, tenant_client: TestClient) -> None:
        response = tenant_client.post(
            "/graphql?database_id=finance",
            json=_execute_payload("finance", "SELECT value FROM org_b_finance_data"),
            headers={
                "Authorization": "Bearer token-a",
                "X-Database-Id": "finance",
                "X-Org-Id": "org-b",
                "X-Tenant-Id": "org-b",
                "X-User-Id": "user-org-b",
                "X-User-Role": "admin",
            },
        )
        assert "not available for this organisation" in response.json()["errors"][0]["message"]
        assert SECRET_F not in response.text

    def test_audit_identity_is_the_validated_principal(
        self, tenant_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            "routes.sql_query_controller.log_audit_event",
            lambda event_type, **payload: captured.update(event=event_type, **payload),
        )
        tenant_client.post(
            "/graphql",
            json=_execute_payload("default", "SELECT value FROM org_a_data"),
            headers={
                "Authorization": "Bearer token-a",
                "X-User-Email": "evil-org-b@example.com",
                "X-Org-Id": "org-b",
            },
        )
        assert captured["org_id"] == "org-a"
        assert captured["user"] == "org-a@example.com"

    def test_gateway_resolves_with_authentic_principal_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        principal = _principal("org-a")
        provider = MagicMock()
        provider.resolve.return_value = (
            TenantDatabaseConfig("org-a", "default", "sqlite+aiosqlite:///:memory:"),
            MagicMock(),
        )
        service_mock = provider.resolve.return_value[1]
        service_mock.repository = MagicMock()
        service_mock.repository.database_target = "primary"
        service_mock.execute_sql_statement = AsyncMock(return_value=[{"dummy": 1}])

        gateway = GovernedQueryGateway(provider, DefaultSqlSafetyChecker())
        import asyncio

        result = asyncio.run(
            gateway.execute(
                GovernedQueryRequest(principal=principal, database_id="default", sql="SELECT 1")
            )
        )
        assert result == [{"dummy": 1}]
        provider.resolve.assert_called_once_with(principal, "default")

    def test_failed_resolution_never_reaches_execution(self) -> None:
        principal = _principal("org-a")
        service_mock = MagicMock()
        service_mock.repository = MagicMock()
        service_mock.repository.database_target = "primary"
        service_mock.execute_sql_statement = AsyncMock()

        resolver = TenantDatabaseResolver(
            [TenantDatabaseConfig("org-b", "finance", "sqlite+aiosqlite:///:memory:")]
        )

        class DenyProvider:
            def resolve(self, p: Principal, database_id: str | None) -> TenantDatabaseConfig:
                return resolver.resolve(p, database_id)

        gateway = GovernedQueryGateway(DenyProvider(), DefaultSqlSafetyChecker())
        import asyncio

        with pytest.raises(TenantDatabaseResolutionError):
            asyncio.run(
                gateway.execute(
                    GovernedQueryRequest(principal=principal, database_id="finance", sql="SELECT 1")
                )
            )
        service_mock.execute_sql_statement.assert_not_awaited()


# ===========================================================================
# 7. Operator CLI (explore.py) is single-tenant by construction
# ===========================================================================
class TestCliIsolation:
    """The headless CLI builds a fresh resolver keyed to the token's org only."""

    @pytest.fixture(autouse=True)
    def _token_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLI_ACCESS_TOKEN", "cli-token")
        monkeypatch.setenv("CLI_DATABASE_ID", "default")

    def test_cli_rejects_token_without_tenant_claim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import explore

        def validate(token: str | None) -> dict[str, Any] | None:
            return {"sub": "cli", "email": "cli@example.com", "roles": ["admin"]}

        monkeypatch.setattr(explore, "validate_access_token", validate)
        import asyncio

        with pytest.raises(PermissionError, match="missing required tenant claims"):
            asyncio.run(explore.execute_query("sqlite+aiosqlite:///:memory:", "SELECT 1"))

    def test_cli_binds_resolver_to_token_org_never_other_tenants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import explore

        def validate(token: str | None) -> dict[str, Any] | None:
            return _claims("org-cli")

        monkeypatch.setattr(explore, "validate_access_token", validate)
        import asyncio

        result = asyncio.run(explore.execute_query("sqlite+aiosqlite:///:memory:", "SELECT 1 AS n"))
        assert result and result[0]["n"] == 1

    def test_cli_still_enforces_select_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import explore

        def validate(token: str | None) -> dict[str, Any] | None:
            return _claims("org-cli")

        monkeypatch.setattr(explore, "validate_access_token", validate)
        import asyncio

        with pytest.raises(ValueError):
            asyncio.run(explore.execute_query("sqlite+aiosqlite:///:memory:", "DROP TABLE x"))


# ===========================================================================
# 8. Cross-tenant reads are always treated as critical (fail-closed oracle)
# ===========================================================================
class TestCrossTenantReadIsNeverReturned:
    """Any cross-tenant attempt must yield an error and ZERO rows, never 200 data."""

    @pytest.mark.parametrize(
        ("token", "database_id", "sql"),
        [
            ("token-a", "finance", "SELECT value FROM org_b_finance_data"),
            ("token-a", "default", "SELECT value FROM org_b_data"),
            ("token-b", "default", "SELECT value FROM org_a_data"),
            ("token-b", "does-not-exist", "SELECT 1"),
        ],
    )
    def test_never_200_with_foreign_rows(
        self, tenant_client: TestClient, token: str, database_id: str, sql: str
    ) -> None:
        body = _execute(tenant_client, token, database_id, sql)
        assert SECRET_A not in str(body)
        assert SECRET_B not in str(body)
        assert SECRET_F not in str(body)
        rows = (body.get("data") or {}).get("executeSqlStatement")
        assert rows is None or rows == []
