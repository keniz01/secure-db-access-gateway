"""
Adversarial hardening tests for the SQL validator.

Exercises the classifier the way an attacker would: SELECT-INTO table
creation, row locks, catalog/schema-qualified reads, quoted identifiers,
forbidden/side-effect functions, comment evasion, quoting and string edge
cases, Unicode, parser stress (deep nesting / control bytes), PostgreSQL-
specific syntax, and confirmation that every execution path — gateway,
repository, and EXPLAIN — passes through the same policy enforcement point.

Each discovered bypass has an explicit regression test, not just a generic
parametrized case.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from auth import Principal
from exceptions.forbidden_sql_statement_exception import ForbiddenSqlStatementError
from repositories.sql_query_repository import SqlQueryRepository
from repositories.sql_validators.ast_analyzer import (
    FORBIDDEN_FUNCTIONS,
    AstSqlAnalyzer,
    normalize_function_name,
)
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import TenantDatabaseConfig


@pytest.fixture(scope="module")
def checker() -> DefaultSqlSafetyChecker:
    return DefaultSqlSafetyChecker()


@pytest.fixture(scope="module")
def sqlite_engine() -> AsyncEngine:
    return create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)


# ---------------------------------------------------------------------------
# Discovered-bypass regression: SELECT ... INTO creates a table in PostgreSQL
# ---------------------------------------------------------------------------
class TestSelectIntoTableCreation:
    """`SELECT ... INTO` is `CREATE TABLE AS` in PostgreSQL and must never pass."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * INTO evil FROM public.tracks",  # discovered bypass
            "SELECT * INTO TEMP foo FROM tracks",  # temp variant
            "SELECT a, b INTO archive FROM tracks WHERE id=1",
            "SELECT 1 INTO x",
            "SELECT * INTO OUTFILE '/tmp/x' FROM tracks",  # foreign-dialect form
            "WITH s AS (SELECT * FROM tracks) SELECT * INTO evil FROM s",
        ],
    )
    def test_select_into_rejected(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is False

    def test_plain_select_still_accepted(self, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query("SELECT * FROM public.tracks") is True


# ---------------------------------------------------------------------------
# Discovered-bypass regression: row locks are not read-only reads
# ---------------------------------------------------------------------------
class TestRowLockRejection:
    """Locking clauses hold rows/sessions and must be rejected by the classifier."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM public.tracks FOR UPDATE",
            "SELECT * FROM public.tracks FOR SHARE",  # discovered bypass
            "SELECT * FROM public.tracks FOR NO KEY UPDATE",
            "SELECT * FROM public.tracks FOR KEY SHARE",
            "SELECT * FROM t FOR UPDATE OF u",
            "SELECT 1 FROM t FOR SHARE",  # lock in projection position
            "SELECT * FROM t1 UNION SELECT * FROM t2 FOR SHARE",
            "WITH x AS (SELECT 1) SELECT * FROM x FOR SHARE",
            "WITH x AS (SELECT * FROM t FOR UPDATE) SELECT * FROM x",  # lock in CTE
        ],
    )
    def test_row_locks_rejected(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is False


# ---------------------------------------------------------------------------
# Discovered-bypass regression: catalog / system-schema reads
# ---------------------------------------------------------------------------
class TestCatalogAndSystemSchemaProtection:
    """pg_catalog / information_schema reads — quoted, qualified, or aliased."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM pg_catalog.pg_class",  # bare qualified (table name pg_*)
            "SELECT * FROM pg_catalog.foo",  # discovered bypass: schema qualifier ignored
            'SELECT * FROM "pg_catalog"."pg_class"',  # discovered bypass: quoted
            'SELECT * FROM "Pg_Catalog"."Pg_Class"',  # quoted + case-mixed
            "SELECT * FROM pg_catalog.pg_class AS c",  # aliased
            "SELECT * FROM pg_toast.pg_toast_2619",  # pg_toast schema
            "SELECT * FROM information_schema.tables",  # discovered bypass: bare name
            'SELECT * FROM "information_schema"."tables"',  # quoted information_schema
            "SELECT * FROM public.t JOIN information_schema.columns c ON 1=1",
            "SELECT c.column_name FROM pg_catalog.pg_attribute c",
        ],
    )
    def test_catalog_reads_rejected(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is False

    def test_quoted_public_tables_still_accepted(self, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query('SELECT * FROM "public"."tracks"') is True
        assert checker.is_safe_select_query('SELECT * FROM "tracks"') is True


# ---------------------------------------------------------------------------
# Discovered-bypass regression: dangerous / side-effect functions
# ---------------------------------------------------------------------------
class TestDangerousAndForbiddenFunctions:
    """File access, delays, locks, large-object writes, dblink, server control."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT pg_catalog.pg_read_file('/etc/passwd')",
            'SELECT "pg_read_file"(\'/etc/passwd\')',  # quoted call still matches
            'SELECT "pg_catalog"."pg_read_file"(\'/etc/passwd\')',
            "SELECT public.pg_read_file('/etc/passwd')",
            "SELECT pg_execute_server_program('ls')",
            "SELECT pg_terminate_backend(1234)",
            "SELECT pg_cancel_backend(1234)",
            "SELECT pg_sleep(5)",
            "SELECT pg_sleep_for('1 second')",
            "SELECT pg_sleep_until('tomorrow')",
            "SELECT lo_from_bytea(0, 'abc')",  # discovered bypass
            "SELECT lo_put(0, 1, 'abc')",  # discovered bypass
            "SELECT lo_create(0)",
            "SELECT lo_unlink(999)",
            "SELECT lo_import('/etc/hosts')",
            "SELECT lo_export(1234, '/tmp/x')",
            "SELECT dblink_exec('conn', 'DROP TABLE x')",
            "SELECT pg_advisory_lock(42)",  # discovered bypass
            "SELECT pg_advisory_xact_lock(42)",
            "SELECT pg_advisory_lock_shared(42, 1)",
            "SELECT pg_try_advisory_lock(42)",
            "SELECT query_to_xml('SELECT 1', false, false, '')",
            "SELECT copy((SELECT 1), '/tmp/x')",
        ],
    )
    def test_forbidden_functions_rejected(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is False

    def test_safe_functions_still_accepted(self, checker: DefaultSqlSafetyChecker) -> None:
        for sql in (
            "SELECT lower(title), upper(title) FROM tracks",
            "SELECT count(*), sum(id) FROM tracks",
            "SELECT now(), current_date, clock_timestamp()",
            "SELECT generate_series(1, 3)",
            "SELECT json_build_object('k', 1)",
            "SELECT coalesce(a, b) FROM tracks",
        ):
            assert checker.is_safe_select_query(sql) is True

    def test_canonical_list_is_single_source_of_truth(self, checker: DefaultSqlSafetyChecker) -> None:
        """Every entry in the canonical set must trip both rule and analyzer layers."""
        analyzer = AstSqlAnalyzer()
        for func in FORBIDDEN_FUNCTIONS:
            q = f"SELECT {func}(1)"
            assert checker.is_safe_select_query(q) is False, func
            assert analyzer.is_strictly_read_only(q) is False, func
        # And the normalizer must reduce qualified/quoted forms to bare names.
        assert normalize_function_name('"pg_catalog"."pg_read_file"') == "pg_read_file"
        assert normalize_function_name("Pg_Catalog.pg_read_file") == "pg_read_file"


# ---------------------------------------------------------------------------
# Comment evasion
# ---------------------------------------------------------------------------
class TestCommentEvasion:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 -- inline",
            "SELECT 1 --\nUNION SELECT 2",
            "SELECT 1 /* block */ UNION SELECT 2",
            "SELECT 1 /* nested /* comment */ */",
            "SELECT 1 /*!50000 UNION SELECT 2 */",  # MySQL-versioned comment
            "SELECT 1 /**/; DROP TABLE x",
            "SELECT 1 /* unterminated",
            "SELECT 1 # hash",  # some dialects treat # as comment start
        ],
    )
    def test_comment_forms_rejected(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is False

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT '--' AS x",  # comment markers INSIDE a string are data
            "SELECT '/*' AS x",
            "SELECT '/* */ --' AS x",
            "SELECT E'--' AS x",
            "SELECT '; DROP TABLE x' AS y",
            "SELECT $$--$$ AS y",
            "SELECT '\\' AS x",
        ],
    )
    def test_comment_markers_inside_strings_allowed(
        self, sql: str, checker: DefaultSqlSafetyChecker
    ) -> None:
        assert checker.is_safe_select_query(sql) is True


# ---------------------------------------------------------------------------
# Quoting and string literal edge cases
# ---------------------------------------------------------------------------
class TestQuotingAndStringEdges:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT $tag$content $; DROP$tag$ AS y",  # tagged dollar-quote
            "SELECT $$; DROP TABLE x$$ AS y",  # semicolon in dollar-quote is data
            "SELECT ';' || 'DROP' AS y",
            "SELECT E'\\141\\142' AS y",  # E'' escape string
            "SELECT 'a''b' AS y",  # doubled single quote
            'SELECT "tracks"."title" FROM "tracks"',  # quoted identifiers
            'SELECT * FROM "a;b"',  # semicolon inside quoted identifier is data
            "SELECT 'café', '日本語', '😀' AS y",
            "SELECT 1;",  # trailing separator is a harmless no-op
            "SELECT 'a\x1fb' AS y",  # control char inside a string literal is data
        ],
    )
    def test_legitimate_strings_and_quoting_accepted(
        self, sql: str, checker: DefaultSqlSafetyChecker
    ) -> None:
        assert checker.is_safe_select_query(sql) is True

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'abc",  # unbalanced single quote
            "SELECT '''''''''",  # quote storm
            "SELECT $$abc",  # unterminated dollar-quote
            "SELECT \"abc",  # unbalanced double quote
            "SELECT (1))",  # unmatched closing paren
            "(':' || ''",
        ],
    )
    def test_malformed_quoting_fails_closed(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is False


# ---------------------------------------------------------------------------
# Unicode inputs
# ---------------------------------------------------------------------------
class TestUnicodeInputs:
    def test_unicode_literals_and_identifiers_accepted(
        self, checker: DefaultSqlSafetyChecker
    ) -> None:
        assert checker.is_safe_select_query("SELECT * FROM tracks WHERE name = 'café'") is True
        assert checker.is_safe_select_query('SELECT "café" FROM "tú"') is True

    @pytest.mark.parametrize(
        "sql",
        [
            "ＳＥＬＥＣＴ * FROM tracks",  # fullwidth SELECT is not SELECT
            "SELECT * FROM tracks WHERE name = 'a' \u200bUNION SELECT 1",  # zero-width space
        ],
    )
    def test_unicode_obfuscation_rejected(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is False


# ---------------------------------------------------------------------------
# Parser edge cases and crash safety (fuzzing regression)
# ---------------------------------------------------------------------------
class TestParserEdgeCaseCrashSafety:
    def test_deep_parenthesis_nesting_never_raises(self, checker: DefaultSqlSafetyChecker) -> None:
        """Regression: deep nesting crashed sqlparse with a RecursionError."""
        query = "SELECT " + "(" * 1500 + "1" + ")" * 1500
        assert checker.is_safe_select_query(query) is False

    @pytest.mark.parametrize(
        "sql",
        [
            "\x00SELECT 1",
            "SELECT 'a\x00b'",
            "SELECT 1;;",
            "SELECT 1 ;\n; DROP TABLE x",
            "SELECT ;SELECT 1",  # leading separator
            "SELECT 1),\t",  # paren + tab garbage
            ";DROP TABLE x; SELECT 1",
        ],
    )
    def test_control_bytes_and_separator_garbage(
        self, sql: str, checker: DefaultSqlSafetyChecker
    ) -> None:
        assert checker.is_safe_select_query(sql) is False

    def test_over_max_length_rejected(self, checker: DefaultSqlSafetyChecker) -> None:
        query = "SELECT 1 -- " + "x" * 20000
        assert checker.is_safe_select_query(query) is False


# ---------------------------------------------------------------------------
# PostgreSQL-specific syntax
# ---------------------------------------------------------------------------
class TestPostgresSpecificSyntax:
    @pytest.mark.parametrize(
        "sql",
        [
            "COPY tracks TO STDOUT",
            "COPY (SELECT * FROM tracks) TO STDOUT WITH CSV",
            "EXPLAIN SELECT * FROM tracks",
            "EXPLAIN ANALYZE SELECT * FROM tracks",
            "TABLE tracks",  # PostgreSQL TABLE command reads a whole table
            "SELECT * FROM tracks FOR UPDATE",  # covered above, kept for completeness
            "SET search_path TO pg_catalog",
            "LISTEN channels;",
        ],
    )
    def test_non_select_postgres_commands_rejected(
        self, sql: str, checker: DefaultSqlSafetyChecker
    ) -> None:
        assert checker.is_safe_select_query(sql) is False

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT DISTINCT ON (title) * FROM tracks ORDER BY title",
            "SELECT * FROM tracks GROUP BY ALL",
            "SELECT * FROM tracks LIMIT 5 OFFSET 2",
            "SELECT * FROM tracks FETCH FIRST 5 ROWS ONLY",
            "WITH x AS MATERIALIZED (SELECT 1) SELECT * FROM x",
            "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v(id, name)",
            "SELECT count(*) FILTER (WHERE id > 0) FROM tracks",
            "SELECT sum(x) OVER (PARTITION BY g ORDER BY x) FROM tracks",
        ],
    )
    def test_legitimate_postgres_selects_accepted(
        self, sql: str, checker: DefaultSqlSafetyChecker
    ) -> None:
        assert checker.is_safe_select_query(sql) is True


# ---------------------------------------------------------------------------
# Nested expressions, aliases, correlated reads (must NOT be over-blocked)
# ---------------------------------------------------------------------------
class TestNestedAndAliasedReads:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM tracks t WHERE EXISTS (SELECT 1 FROM albums a WHERE a.id = t.album_id)",
            "SELECT t.title, a.name FROM tracks AS t JOIN albums AS a ON a.id = t.album_id",
            "SELECT * FROM (SELECT * FROM tracks WHERE id < 10) AS derived",
            "SELECT * FROM tracks t1, albums t2 WHERE t1.id = t2.id",
            "SELECT (SELECT max(id) FROM tracks) AS max_id",
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<3) SELECT * FROM x",
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM tracks) THEN 'y' ELSE 'n' END",
            "SELECT * FROM albums a WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = a.id)",
        ],
    )
    def test_analytiv_reads_accepted(self, sql: str, checker: DefaultSqlSafetyChecker) -> None:
        assert checker.is_safe_select_query(sql) is True

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH d AS (DELETE FROM tracks RETURNING *) SELECT * FROM d",  # mutating CTE
            "SELECT * FROM tracks WHERE id IN (SELECT id FROM (DELETE FROM other RETURNING id) x)",
            "WITH u AS (UPDATE tracks SET title='x' RETURNING *) SELECT * FROM u",
            "SELECT * FROM tracks WHERE id = (INSERT INTO log VALUES (1) RETURNING id)",
            "WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM i",
        ],
    )
    def test_mutating_cte_and_subquery_rejected(
        self, sql: str, checker: DefaultSqlSafetyChecker
    ) -> None:
        assert checker.is_safe_select_query(sql) is False


# ---------------------------------------------------------------------------
# Every execution path funnels through the same enforcement point
# ---------------------------------------------------------------------------
class TestSingleEnforcementPoint:
    """Gateway, repository, and EXPLAIN must all reject the same bypass payloads."""

    BYPASSES = [
        "SELECT * INTO evil FROM public.tracks",
        "SELECT * FROM public.tracks FOR SHARE",
        "SELECT * FROM pg_catalog.pg_class",
        "SELECT pg_catalog.pg_read_file('/etc/passwd')",
        "SELECT pg_advisory_lock(42)",
        "SELECT lo_from_bytea(0, 'abc')",
        "SELECT 1; DROP TABLE tracks",
        "SELECT * FROM tracks -- comment",
    ]

    def _make_gateway(self) -> tuple[GovernedQueryGateway, MagicMock]:
        provider = MagicMock()
        service = MagicMock()
        service.repository.database_target = "primary"
        service.execute_sql_statement = AsyncMock()
        provider.resolve.return_value = (
            TenantDatabaseConfig("org-1", "analytics", "sqlite+aiosqlite:///:memory:"),
            service,
        )
        gateway = GovernedQueryGateway(provider, DefaultSqlSafetyChecker())
        return gateway, service

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sql", BYPASSES)
    async def test_gateway_rejects_before_resolving(
        self, sql: str, checker: DefaultSqlSafetyChecker
    ) -> None:
        principal = Principal("user-1", "user@example.com", "org-1", frozenset({"viewer"}))
        gateway, service = self._make_gateway()
        with pytest.raises(ValueError, match="safety validation"):
            await gateway.execute(
                GovernedQueryRequest(principal=principal, database_id="analytics", sql=sql)
            )
        service.execute_sql_statement.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sql", BYPASSES)
    async def test_repository_execute_rejects(
        self, sql: str, checker: DefaultSqlSafetyChecker, sqlite_engine: AsyncEngine
    ) -> None:
        repo = SqlQueryRepository(sqlite_engine, checker)
        with pytest.raises(ForbiddenSqlStatementError):
            await repo.execute_sql_statement(sql)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sql", BYPASSES)
    async def test_explain_path_rejects(
        self, sql: str, checker: DefaultSqlSafetyChecker, sqlite_engine: AsyncEngine
    ) -> None:
        repo = SqlQueryRepository(sqlite_engine, checker)
        with pytest.raises(ForbiddenSqlStatementError):
            await repo.explain_query_cost(sql)

    @pytest.mark.asyncio
    async def test_explain_still_works_for_safe_select(
        self, checker: DefaultSqlSafetyChecker, sqlite_engine: AsyncEngine
    ) -> None:
        async with sqlite_engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        repo = SqlQueryRepository(sqlite_engine, checker)
        result = await repo.explain_query_cost("SELECT * FROM t")
        assert result["supported"] is False  # sqlite has no EXPLAIN (FORMAT JSON)
        assert result["dialect"] == "sqlite"
