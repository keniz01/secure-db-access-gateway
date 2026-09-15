"""Tests for Production Readiness Phase 1: AST parsing, limits, audit sanitization, and DB constraints."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import Principal
from exceptions.sql_statement_execution_exception import SqlStatementExecutionError
from repositories.sql_query_repository import SqlQueryRepository
from repositories.sql_validators.ast_analyzer import AstSqlAnalyzer
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import TenantDatabaseConfig


class TestAstSqlAnalyzer:
    """Validate AST parser against advanced SQL structures and bypass attempts."""

    @pytest.fixture
    def analyzer(self) -> AstSqlAnalyzer:
        return AstSqlAnalyzer()

    def test_single_statement_detection(self, analyzer: AstSqlAnalyzer) -> None:
        assert analyzer.is_single_statement("SELECT 1") is True
        assert analyzer.is_single_statement("SELECT 1; SELECT 2") is False
        assert analyzer.is_single_statement("SELECT 1; DROP TABLE users") is False

    def test_extract_tables_with_ctes(self, analyzer: AstSqlAnalyzer) -> None:
        sql = (
            "WITH regional_sales AS (SELECT * FROM sales WHERE region = 'west') "
            "SELECT * FROM regional_sales JOIN customers ON regional_sales.cust_id = customers.id"
        )
        tables = analyzer.extract_tables(sql)
        # regional_sales is a CTE, so only real physical tables (sales, customers) must be returned
        assert "sales" in tables
        assert "customers" in tables
        assert "regional_sales" not in tables

    def test_extract_tables_with_subqueries_and_quotes(self, analyzer: AstSqlAnalyzer) -> None:
        sql = 'SELECT * FROM "public"."orders" o JOIN (SELECT id, name FROM "inventory"."items") i ON o.item_id = i.id'
        tables = analyzer.extract_tables(sql)
        assert "orders" in tables
        assert "items" in tables

    def test_reject_mutating_cte(self, analyzer: AstSqlAnalyzer) -> None:
        sql = "WITH deleted_rows AS (DELETE FROM users WHERE id = 1 RETURNING *) SELECT * FROM deleted_rows"
        assert analyzer.is_strictly_read_only(sql) is False

    def test_reject_forbidden_functions(self, analyzer: AstSqlAnalyzer) -> None:
        assert analyzer.is_strictly_read_only("SELECT pg_read_file('/etc/passwd')") is False
        assert analyzer.is_strictly_read_only("SELECT dblink_connect('conn', 'host=localhost')") is False
        assert analyzer.is_strictly_read_only("SELECT pg_write_file('shell.php', '<?php phpinfo(); ?>')") is False

    def test_allow_safe_functions(self, analyzer: AstSqlAnalyzer) -> None:
        assert analyzer.is_strictly_read_only("SELECT lower(name), count(*) FROM users GROUP BY lower(name)") is True
        assert analyzer.is_strictly_read_only("SELECT coalesce(nickname, name, 'Anonymous') FROM profiles") is True


class TestQueryGatewayAuditSanitization:
    """Ensure raw SQL is sanitized into query_hash by default."""

    @pytest.mark.asyncio
    async def test_audit_logs_query_hash_not_raw_sql_by_default(self) -> None:
        principal = Principal("user-1", "user@example.com", "org-1", frozenset({"viewer"}))
        binding = TenantDatabaseConfig("org-1", "db-1", "sqlite+aiosqlite:///:memory:")
        service = MagicMock()
        service.repository.database_target = "primary"
        service.execute_sql_statement = AsyncMock(return_value=[{"id": 1}])
        provider = MagicMock()
        provider.resolve.return_value = (binding, service)
        audit_mock = MagicMock()

        gateway = GovernedQueryGateway(
            provider,
            DefaultSqlSafetyChecker(),
            audit=audit_mock,
        )

        sensitive_sql = "SELECT * FROM users WHERE email = 'sensitive_customer_pii@example.com'"
        await gateway.execute(
            GovernedQueryRequest(
                principal=principal,
                database_id="db-1",
                sql=sensitive_sql,
            )
        )

        audit_mock.assert_called_once()
        args, kwargs = audit_mock.call_args
        assert args == ("sql_query",)
        assert "query_hash" in kwargs
        assert len(kwargs["query_hash"]) == 64  # SHA-256 hex string
        assert "query" not in kwargs  # Raw SQL containing email must NOT be present
        assert kwargs["tables_touched"] == ["users"]


class TestQueryResultResourceLimits:
    """Validate max row and max byte serialization enforcement."""

    @pytest.mark.asyncio
    async def test_repository_enforces_max_row_limit(self) -> None:
        engine = MagicMock()
        conn = AsyncMock()
        conn.dialect.name = "sqlite"

        # Return 6 rows when max is set to 5
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.fetchmany.side_effect = [
            [MagicMock(_mapping={"id": i, "data": f"row-{i}"}) for i in range(6)],
            [],
        ]
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = AsyncMock(return_value=conn)

        repo = SqlQueryRepository(
            engine=engine,
            sql_safety_checker=DefaultSqlSafetyChecker(),
        )
        repo._max_row_limit = 5

        with pytest.raises(SqlStatementExecutionError, match="exceeds the maximum allowed row limit"):
            await repo.execute_sql_statement("SELECT * FROM large_table")

    @pytest.mark.asyncio
    async def test_repository_enforces_max_result_bytes(self) -> None:
        engine = MagicMock()
        conn = AsyncMock()
        conn.dialect.name = "sqlite"

        # Return a payload exceeding byte size
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.fetchmany.side_effect = [
            [MagicMock(_mapping={"id": 1, "blob": "X" * 1000})],
            [],
        ]
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = AsyncMock(return_value=conn)

        repo = SqlQueryRepository(
            engine=engine,
            sql_safety_checker=DefaultSqlSafetyChecker(),
        )
        repo._max_result_bytes = 500  # 500 bytes max

        with pytest.raises(SqlStatementExecutionError, match="exceeds maximum allowed size"):
            await repo.execute_sql_statement("SELECT id, blob FROM large_blob_table")
