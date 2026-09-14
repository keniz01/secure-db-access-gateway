import pytest
from app.services.text_to_sql_service import (
    SqlExecutionError,
    TextToSqlService,
    _is_retryable_execution_error,
)
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

@pytest.fixture
def text_to_sql_service(mock_ai_service):
    return TextToSqlService(mock_ai_service)

@pytest.mark.asyncio
async def test_generate_sql_from_text_success(text_to_sql_service, mock_ai_service):
    """Test successful SQL generation from text."""
    # Mock schema retrieval
    mock_schema = "CREATE TABLE users (id INT, name TEXT)"
    
    # Mock GraphQL response
    mock_gql_response = MagicMock()
    mock_gql_response.status_code = 200
    mock_gql_response.json.return_value = {
        "data": {
            "getTableSchema": {
                "schema": mock_schema
            }
        }
    }
    mock_gql_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient.post", return_value=mock_gql_response):
        # Mock LLM SQL generation
        mock_ai_service.get_greeting.return_value = "SELECT * FROM users"
        
        result = await text_to_sql_service.generate_sql_from_text("get all users")
        
        assert result["sql"] == "SELECT * FROM users"
        assert result["schema"] == mock_schema
        mock_ai_service.generate_embeddings.assert_called_once_with("get all users")
        mock_ai_service.get_greeting.assert_called_once()

@pytest.mark.asyncio
async def test_generate_sql_with_execution(text_to_sql_service, mock_ai_service):
    """Test SQL generation and execution."""
    mock_schema = "TABLE schema"
    mock_sql = "SELECT 1"
    mock_results = [{"col": 1}]
    
    # Mock GraphQL responses for both schema and execution
    mock_schema_response = MagicMock()
    mock_schema_response.status_code = 200
    mock_schema_response.json.return_value = {"data": {"getTableSchema": {"schema": mock_schema}}}
    
    mock_exec_response = MagicMock()
    mock_exec_response.status_code = 200
    mock_exec_response.json.return_value = {"data": {"executeSqlStatement": mock_results}}
    
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.side_effect = [mock_schema_response, mock_exec_response]
        mock_ai_service.get_greeting.return_value = mock_sql
        
        result = await text_to_sql_service.generate_sql_from_text("test query", execute=True)
        
        assert result["sql"] == mock_sql
        assert result["results"] == mock_results
        assert mock_post.call_count == 2

@pytest.mark.asyncio
async def test_generate_sql_graphql_error(text_to_sql_service, mock_ai_service):
    """Test SQL generation when GraphQL returns error."""
    mock_error_response = MagicMock()
    mock_error_response.status_code = 200
    mock_error_response.json.return_value = {"errors": [{"message": "GraphQL Error"}]}
    
    with patch("httpx.AsyncClient.post", return_value=mock_error_response):
        result = await text_to_sql_service.generate_sql_from_text("test query")
        
        assert "error" in result
        assert "GraphQL Error" in result["error"]

@pytest.mark.asyncio
async def test_generate_sql_http_error(text_to_sql_service, mock_ai_service):
    """Test SQL generation when HTTP request fails."""
    with patch("httpx.AsyncClient.post", side_effect=httpx.HTTPError("Connection failed")):
        result = await text_to_sql_service.generate_sql_from_text("test query")

        assert "error" in result
        assert "Connection failed" in result["error"]


def test_precheck_sql_accepts_single_select():
    ok, feedback = TextToSqlService._precheck_sql("SELECT * FROM users")
    assert ok is True
    assert feedback == ""


def test_precheck_sql_accepts_trailing_semicolon():
    ok, _ = TextToSqlService._precheck_sql("SELECT * FROM users;")
    assert ok is True


def test_precheck_sql_accepts_semicolon_inside_string():
    ok, _ = TextToSqlService._precheck_sql("SELECT title FROM album WHERE title ILIKE 'Get; Up'")
    assert ok is True


def test_precheck_sql_rejects_multi_statement():
    ok, feedback = TextToSqlService._precheck_sql(
        "SELECT COUNT(*) FROM artist; SELECT title FROM album;"
    )
    assert ok is False
    assert "Multiple SQL statements" in feedback


def test_precheck_sql_strips_markdown_and_prefix():
    ok, _ = TextToSqlService._precheck_sql("```sql\nSELECT * FROM users\n```")
    assert ok is True
    ok, _ = TextToSqlService._precheck_sql("SQL: SELECT * FROM users")
    assert ok is True


def test_precheck_sql_rejects_non_select():
    ok, feedback = TextToSqlService._precheck_sql("DELETE FROM users")
    assert ok is False
    assert "SELECT" in feedback


@pytest.mark.asyncio
async def test_generate_sql_retries_on_multi_statement(text_to_sql_service, mock_ai_service):
    """Multi-statement output triggers a retry that produces a valid SELECT."""
    mock_schema = "TABLE schema"
    mock_gql_response = MagicMock()
    mock_gql_response.status_code = 200
    mock_gql_response.json.return_value = {"data": {"getTableSchema": {"schema": mock_schema}}}
    mock_gql_response.raise_for_status = MagicMock()

    mock_ai_service.get_greeting.side_effect = [
        "SELECT COUNT(*) FROM artist; SELECT title FROM album;",
        "SELECT (SELECT COUNT(*) FROM artist) AS count, (SELECT title FROM album LIMIT 1) AS title;",
    ]

    with patch("httpx.AsyncClient.post", return_value=mock_gql_response):
        result = await text_to_sql_service.generate_sql_from_text("multi part query")

    assert result["sql"] == (
        "SELECT (SELECT COUNT(*) FROM artist) AS count, (SELECT title FROM album LIMIT 1) AS title;"
    )
    assert mock_ai_service.get_greeting.call_count == 2


@pytest.mark.asyncio
async def test_generate_sql_retry_exhausted_returns_error(text_to_sql_service, mock_ai_service):
    """Persistently invalid output surfaces the pre-check feedback as an error."""
    mock_schema = "TABLE schema"
    mock_gql_response = MagicMock()
    mock_gql_response.status_code = 200
    mock_gql_response.json.return_value = {"data": {"getTableSchema": {"schema": mock_schema}}}
    mock_gql_response.raise_for_status = MagicMock()

    mock_ai_service.get_greeting.side_effect = [
        "SELECT COUNT(*) FROM artist; SELECT title FROM album;",
        "SELECT COUNT(*) FROM artist; SELECT title FROM album;",
    ]

    with patch("httpx.AsyncClient.post", return_value=mock_gql_response):
        result = await text_to_sql_service.generate_sql_from_text("multi part query")

    assert "error" in result
    assert "Multiple SQL statements" in result["error"]
    assert mock_ai_service.get_greeting.call_count == 2


def test_precheck_sql_rejects_undeclared_alias():
    """A column qualifier with no matching FROM/JOIN alias must be flagged."""
    ok, feedback = TextToSqlService._precheck_sql(
        "SELECT COUNT(a.album_id) AS album_count, "
        "(SELECT a2.title FROM album a2 WHERE a2.artist_id = ar.artist_id "
        "ORDER BY a2.total_tracks DESC LIMIT 1) AS album_with_most_tracks "
        "FROM artist ar "
        "LEFT JOIN album al ON al.artist_id = ar.artist_id "
        "WHERE ar.artist_name ILIKE 'Mavado';"
    )
    assert ok is False
    assert '"a"' in feedback
    assert "declared" in feedback


def test_precheck_sql_accepts_consistent_aliases():
    """A query where every qualifier matches a declared alias passes."""
    ok, feedback = TextToSqlService._precheck_sql(
        "SELECT ar.artist_name, al.title "
        "FROM artist ar "
        "INNER JOIN album al ON al.artist_id = ar.artist_id "
        "WHERE ar.artist_name ILIKE 'Mavado';"
    )
    assert ok is True
    assert feedback == ""


def test_precheck_sql_accepts_joined_real_names():
    ok, _ = TextToSqlService._precheck_sql(
        "SELECT tr.title FROM track tr "
        "INNER JOIN album al ON al.album_id = tr.album_id "
        "WHERE al.title ILIKE 'Soca Xplosion 2007';"
    )
    assert ok is True


def test_is_retryable_execution_error_classification():
    assert _is_retryable_execution_error("missing FROM-clause entry for table a")
    assert _is_retryable_execution_error('UndefinedColumnError: column "x" does not exist')
    assert _is_retryable_execution_error('OperationalError: no such table: foo')
    assert _is_retryable_execution_error('GroupingError: subquery uses ungrouped column "ar.artist_id" from outer query')
    assert not _is_retryable_execution_error("SQL query timed out after 30.0 seconds.")
    assert not _is_retryable_execution_error("")


def test_precheck_rejects_ungrouped_correlated_subquery():
    """The Mavado regressor: top-level COUNT + correlated subquery, no GROUP BY."""
    ok, feedback = TextToSqlService._precheck_sql(
        "SELECT COUNT(al.album_id) AS album_count, "
        "(SELECT a2.title FROM album a2 WHERE a2.artist_id = ar.artist_id "
        "ORDER BY a2.total_tracks DESC LIMIT 1) AS album_with_most_tracks "
        "FROM artist ar "
        "INNER JOIN album al ON al.artist_id = ar.artist_id "
        "WHERE ar.artist_name ILIKE 'Mavado';"
    )
    assert ok is False
    assert "GROUP BY" in feedback


def test_precheck_accepts_correlated_subquery_with_group_by():
    """Adding GROUP BY that covers the referenced outer column makes it valid."""
    ok, _ = TextToSqlService._precheck_sql(
        "SELECT ar.artist_id, COUNT(al.album_id) AS album_count, "
        "(SELECT a2.title FROM album a2 WHERE a2.artist_id = ar.artist_id "
        "ORDER BY a2.total_tracks DESC LIMIT 1) AS album_with_most_tracks "
        "FROM artist ar "
        "INNER JOIN album al ON al.artist_id = ar.artist_id "
        "WHERE ar.artist_name ILIKE 'Mavado' "
        "GROUP BY ar.artist_id;"
    )
    assert ok is True


def test_precheck_accepts_aggregate_with_non_correlated_subquery():
    """A scalar subquery that does not reference outer columns is valid."""
    ok, _ = TextToSqlService._precheck_sql(
        "SELECT COUNT(*) AS count, "
        "(SELECT MAX(total_tracks) FROM album) AS max_tracks FROM album;"
    )
    assert ok is True


def test_precheck_accepts_plain_aggregate_without_subquery():
    ok, _ = TextToSqlService._precheck_sql(
        "SELECT ar.artist_name, COUNT(al.album_id) AS album_count "
        "FROM artist ar INNER JOIN album al ON al.artist_id = ar.artist_id "
        "GROUP BY ar.artist_name;"
    )
    assert ok is True


@pytest.mark.asyncio
async def test_execute_sql_raises_sql_execution_error_with_detail(text_to_sql_service):
    """Execution failures with a sqlQueryApi extension surface SqlExecutionError."""
    mock_error = MagicMock()
    mock_error.status_code = 200
    mock_error.raise_for_status = MagicMock()
    mock_error.json.return_value = {
        "errors": [
            {
                "message": "Failed to execute SQL statement. Please verify your query syntax.",
                "extensions": {
                    "sqlQueryApi": {
                        "code": "SQL_EXECUTION_ERROR",
                        "details": 'missing FROM-clause entry for table "a"',
                    }
                },
            }
        ]
    }

    with patch("httpx.AsyncClient.post", return_value=mock_error):
        with pytest.raises(SqlExecutionError) as excinfo:
            await text_to_sql_service._execute_sql("SELECT * FROM t")

    assert 'missing FROM-clause entry for table "a"' in excinfo.value.detail


def _gql_response(payload):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = payload
    return mock_resp


def _sql_execution_error(detail: str):
    return _gql_response(
        {
            "errors": [
                {
                    "message": "Failed to execute SQL statement. Please verify your query syntax.",
                    "extensions": {
                        "sqlQueryApi": {"code": "SQL_EXECUTION_ERROR", "details": detail}
                    },
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_generate_sql_with_execution_retries_on_db_error(
    text_to_sql_service, mock_ai_service
):
    """A correctable DB error triggers a corrective LLM round-trip."""
    mock_schema = "TABLE schema"
    mock_ai_service.get_greeting.side_effect = [
        "SELECT COUNT(al.album_id) AS album_count FROM album al;",
        "SELECT COUNT(al.id) AS album_count FROM album al;",
    ]

    error_resp = _sql_execution_error('UndefinedColumnError: column "album_id" does not exist')
    ok_resp = _gql_response({"data": {"executeSqlStatement": [{"album_count": 3}]}})
    schema_resp = _gql_response({"data": {"getTableSchema": {"schema": mock_schema}}})

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.side_effect = [schema_resp, error_resp, ok_resp]
        result = await text_to_sql_service.generate_sql_from_text("test query", execute=True)

    assert result["sql"] == "SELECT COUNT(al.id) AS album_count FROM album al;"
    assert result["results"] == [{"album_count": 3}]
    assert mock_ai_service.get_greeting.call_count == 2

    second_user_prompt = mock_ai_service.get_greeting.call_args_list[1].kwargs["user"]
    assert 'column "album_id" does not exist' in second_user_prompt


@pytest.mark.asyncio
async def test_generate_sql_with_execution_does_not_retry_non_retryable_error(
    text_to_sql_service, mock_ai_service
):
    """Timeouts and other non-correctable failures return an error immediately."""
    mock_schema = "TABLE schema"
    mock_ai_service.get_greeting.return_value = (
        "SELECT COUNT(al.album_id) AS album_count FROM album al;"
    )

    error_resp = _sql_execution_error("SQL query timed out after 30.0 seconds.")
    schema_resp = _gql_response({"data": {"getTableSchema": {"schema": mock_schema}}})

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.side_effect = [schema_resp, error_resp]
        result = await text_to_sql_service.generate_sql_from_text("test query", execute=True)

    assert "error" in result
    assert "timed out" in result["error"]
    assert result["sql"] == "SELECT COUNT(al.album_id) AS album_count FROM album al;"
    assert mock_ai_service.get_greeting.call_count == 1


@pytest.mark.asyncio
async def test_generate_sql_with_execution_exhausts_retries(
    text_to_sql_service, mock_ai_service
):
    """Persistently failing correctable errors stop after MAX_EXECUTION_ATTEMPTS."""
    mock_schema = "TABLE schema"
    mock_ai_service.get_greeting.side_effect = [
        "SELECT COUNT(al.album_id) FROM album al;",
        "SELECT COUNT(al.album_id) FROM album al;",
        "SELECT COUNT(al.album_id) FROM album al;",
    ]

    error_resp = _sql_execution_error('UndefinedColumnError: column "album_id" does not exist')
    schema_resp = _gql_response({"data": {"getTableSchema": {"schema": mock_schema}}})

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.side_effect = [schema_resp, error_resp, error_resp, error_resp]
        result = await text_to_sql_service.generate_sql_from_text("test query", execute=True)

    assert "error" in result
    assert 'column "album_id" does not exist' in result["error"]
    assert mock_ai_service.get_greeting.call_count == 3
