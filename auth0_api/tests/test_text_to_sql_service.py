import pytest
from app.services.text_to_sql_service import TextToSqlService
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
