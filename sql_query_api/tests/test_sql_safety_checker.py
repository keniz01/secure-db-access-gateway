import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import Principal
from exceptions.sql_statement_execution_exception import SqlStatementExecutionException
from repositories.sql_query_repository import SqlQueryRepository
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.policy_engine import apply_row_restrictions, mask_rows


@pytest.fixture
def checker() -> DefaultSqlSafetyChecker:
    return DefaultSqlSafetyChecker()


class TestSqlSafetyCheckerAllowedSelects:
    """Test suite for allowed, safe SELECT statements."""

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM artist",
            "SELECT id, name FROM artist WHERE id = 1",
            "SELECT a.name, b.title FROM artist a JOIN album b ON a.id = b.artist_id",
            "SELECT genre, COUNT(*) FROM track GROUP BY genre HAVING COUNT(*) > 5 ORDER BY COUNT(*) DESC",
            "SELECT * FROM track LIMIT 10 OFFSET 20",
            "select title from album",
            "SELECT artist.name AS artist_name FROM artist",
        ],
    )
    def test_allowed_select_queries(self, checker: DefaultSqlSafetyChecker, query: str) -> None:
        assert checker.is_safe_select_query(query) is True


class TestSqlSafetyCheckerRejectedDDLAndDML:
    """Test suite for rejecting DDL, DML, and state-altering statements."""

    @pytest.mark.parametrize(
        "query",
        [
            "INSERT INTO artist (name) VALUES ('Test')",
            "UPDATE artist SET name = 'Test' WHERE id = 1",
            "DELETE FROM artist WHERE id = 1",
            "CREATE TABLE foo (id INT)",
            "ALTER TABLE artist ADD COLUMN bio TEXT",
            "DROP TABLE artist",
            "TRUNCATE TABLE track",
            "COMMIT;",
            "ROLLBACK;",
            "GRANT SELECT ON artist TO public",
        ],
    )
    def test_rejected_ddl_dml(self, checker: DefaultSqlSafetyChecker, query: str) -> None:
        assert checker.is_safe_select_query(query) is False


class TestSqlSafetyCheckerAllowsAnalyticalSubqueriesAndCTEs:
    """Analytical read queries may use subqueries and CTEs when they remain read-only."""

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM artist WHERE id IN (SELECT artist_id FROM album)",
            "SELECT * FROM (SELECT * FROM artist) AS sub",
            "WITH cte AS (SELECT * FROM artist) SELECT * FROM cte",
            "SELECT (SELECT name FROM artist LIMIT 1) FROM album",
            "SELECT * FROM artist UNION SELECT * FROM artist",
            "SELECT * FROM artist INTERSECT SELECT * FROM artist",
            "SELECT * FROM artist EXCEPT SELECT * FROM artist",
        ],
    )
    def test_allowed_analytical_queries(self, checker: DefaultSqlSafetyChecker, query: str) -> None:
        assert checker.is_safe_select_query(query) is True


class TestSqlSafetyCheckerRejectedCommentsAndMultiStatements:
    """Comments and multi-statement inputs remain blocked."""

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM artist -- inline comment",
            "SELECT * FROM artist /* block comment */",
            "SELECT * FROM artist; SELECT * FROM album;",
            "SELECT * FROM artist; DROP TABLE artist;",
            "WITH cte AS (DELETE FROM artist WHERE id = 1) SELECT * FROM cte",
            "SELECT * FROM artist WHERE id IN (DELETE FROM artist WHERE id = 1)",
        ],
    )
    def test_rejected_comments_multistatements_and_mutating_subqueries(
        self, checker: DefaultSqlSafetyChecker, query: str
    ) -> None:
        assert checker.is_safe_select_query(query) is False


class TestSqlSafetyCheckerLengthAndEmptyLimits:
    """Test suite for query length limits and empty query handling."""

    def test_empty_or_whitespace_query(self, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query("") is False
        assert checker.is_safe_select_query("   ") is False
        assert checker.is_safe_select_query("\n\t") is False

    def test_exceeds_max_query_length(self) -> None:
        custom_checker = DefaultSqlSafetyChecker(max_query_length=50)
        long_query = "SELECT * FROM artist WHERE name = '" + "A" * 60 + "'"
        assert custom_checker.is_safe_select_query(long_query) is False

    def test_default_max_query_length(self, checker: DefaultSqlSafetyChecker) -> None:
        oversized_query = "SELECT * FROM artist WHERE name = '" + "X" * 10005 + "'"
        assert checker.is_safe_select_query(oversized_query) is False


class TestSqlSafetyCheckerCleanAndValidate:
    """Test suite for LLM output cleaning and validation."""

    def test_clean_and_validate_markdown_sql(self, checker: DefaultSqlSafetyChecker) -> None:
        raw_llm_output = "```sql\nSELECT * FROM artist\n```"
        cleaned = checker.clean_and_validate_sql(raw_llm_output)
        assert cleaned == "SELECT * FROM artist"

    def test_clean_and_validate_raises_on_invalid_sql(self, checker: DefaultSqlSafetyChecker) -> None:
        unsafe_raw_sql = "DELETE FROM artist WHERE id = 1"
        with pytest.raises(ValueError, match="SQL query failed safety validation"):
            checker.clean_and_validate_sql(unsafe_raw_sql)

    def test_rejects_table_not_in_allowlist(self) -> None:
        checker = DefaultSqlSafetyChecker(table_allowlist={"artist"})
        assert checker.is_safe_select_query("SELECT * FROM artist") is True
        assert checker.is_safe_select_query("SELECT * FROM album") is False

    def test_rejects_disallowed_column(self) -> None:
        checker = DefaultSqlSafetyChecker(column_allowlist={"artist": {"name"}})
        assert checker.is_safe_select_query("SELECT name FROM artist") is True
        assert checker.is_safe_select_query("SELECT id, name FROM artist") is False

    @pytest.mark.asyncio
    async def test_masks_sensitive_columns_in_results(self) -> None:
        engine = MagicMock()
        conn = AsyncMock()
        conn.dialect.name = "sqlite"

        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.fetchall.return_value = [
            MagicMock(_mapping={"email": "alice@example.com", "name": "Alice"}),
        ]
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = AsyncMock(return_value=conn)

        repo = SqlQueryRepository(
            engine=engine,
            sql_safety_checker=DefaultSqlSafetyChecker(),
            sensitive_columns={"email"},
        )

        rows = await repo.execute_sql_statement("SELECT email, name FROM users")
        assert rows[0]["email"] == "[MASKED]"
        assert rows[0]["name"] == "Alice"

    def test_estimate_cost_low_and_high_queries(self) -> None:
        repo = SqlQueryRepository(engine=MagicMock(), sql_safety_checker=DefaultSqlSafetyChecker())
        assert repo.estimate_query_cost("SELECT id FROM artist LIMIT 10")["level"] == "low"
        high_cost = repo.estimate_query_cost("SELECT * FROM artist a JOIN album b ON a.id = b.artist_id WHERE a.id IN (SELECT artist_id FROM album)")
        assert high_cost["level"] in {"high", "critical"}

    @pytest.mark.asyncio
    async def test_row_filter_reduces_results(self) -> None:
        engine = MagicMock()
        conn = AsyncMock()
        conn.dialect.name = "sqlite"

        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.fetchall.return_value = [
            MagicMock(_mapping={"tenant_id": 1, "name": "Alice"}),
            MagicMock(_mapping={"tenant_id": 2, "name": "Bob"}),
        ]
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = AsyncMock(return_value=conn)

        repo = SqlQueryRepository(
            engine=engine,
            sql_safety_checker=DefaultSqlSafetyChecker(),
            row_filter=lambda row: row.get("tenant_id") == 1,
        )

        rows = await repo.execute_sql_statement("SELECT tenant_id, name FROM users")
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == 1

    def test_policy_row_restrictions_qualify_join_tables(self) -> None:
        principal = Principal(
            user_id="u1",
            email="u1@example.com",
            org_id="org-1",
            roles=frozenset({"viewer"}),
            attributes={"region": "EU"},
        )
        sql = "SELECT o.region, c.name FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.country = 'FR'"
        restricted = apply_row_restrictions(sql, {"region": "region"}, principal)
        assert "o.region = 'EU'" in restricted
        assert "c.region = 'EU'" in restricted

    def test_mask_rows_masks_derived_aliases(self) -> None:
        rows = [{"customer_email": "alice@example.com", "name": "Alice"}]
        masked = mask_rows(rows, frozenset({"email"}), "SELECT CONCAT(email, '@example.com') AS customer_email, name FROM users")
        assert masked[0]["customer_email"] is None
        assert masked[0]["name"] == "Alice"


class TestSqlQueryTimeout:
    """Test suite for query timeout enforcement."""

    @pytest.mark.asyncio
    async def test_repository_uses_configured_timeout(self, checker: DefaultSqlSafetyChecker) -> None:
        repo = SqlQueryRepository(
            engine=MagicMock(),
            sql_safety_checker=checker,
            query_timeout_seconds=12.5,
        )
        assert repo._query_timeout_seconds == 12.5

    @pytest.mark.asyncio
    async def test_execute_sql_statement_raises_on_timeout(
        self, checker: DefaultSqlSafetyChecker
    ) -> None:
        engine = MagicMock()
        conn = AsyncMock()
        conn.dialect.name = "sqlite"

        async def timeout_execute(*args, **kwargs):
            await asyncio.sleep(0.05)
            raise asyncio.TimeoutError()

        conn.execute.side_effect = timeout_execute
        engine.connect = AsyncMock(return_value=conn)

        repo = SqlQueryRepository(
            engine=engine,
            sql_safety_checker=checker,
            query_timeout_seconds=0.01,
        )

        with pytest.raises(SqlStatementExecutionException, match="timed out"):
            await repo.execute_sql_statement("SELECT 1")
