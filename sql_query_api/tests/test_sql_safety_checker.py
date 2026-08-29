import pytest
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker


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


class TestSqlSafetyCheckerRejectedSubqueriesAndCTEs:
    """Test suite for rejecting nested subqueries and Common Table Expressions (CTEs)."""

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM artist WHERE id IN (SELECT artist_id FROM album)",
            "SELECT * FROM (SELECT * FROM artist) AS sub",
            "WITH cte AS (SELECT * FROM artist) SELECT * FROM cte",
            "SELECT (SELECT name FROM artist LIMIT 1) FROM album",
        ],
    )
    def test_rejected_subqueries_and_ctes(self, checker: DefaultSqlSafetyChecker, query: str) -> None:
        assert checker.is_safe_select_query(query) is False


class TestSqlSafetyCheckerRejectedCommentsAndMultiStatements:
    """Test suite for rejecting comments, set operations, and multiple statements."""

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM artist -- inline comment",
            "SELECT * FROM artist /* block comment */",
            "SELECT * FROM artist; SELECT * FROM album;",
            "SELECT * FROM artist; DROP TABLE artist;",
            "SELECT * FROM artist UNION SELECT * FROM artist",
            "SELECT * FROM artist INTERSECT SELECT * FROM artist",
            "SELECT * FROM artist EXCEPT SELECT * FROM artist",
        ],
    )
    def test_rejected_comments_set_ops_multistatements(
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
