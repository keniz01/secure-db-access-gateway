import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_get_user_authenticated(client, authenticated_session):
    """Test get user endpoint when authenticated."""
    response = await client.get("/api/user")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == authenticated_session["user"]["email"]
    assert data["name"] == authenticated_session["user"]["name"]

@pytest.mark.asyncio
async def test_get_user_unauthenticated(client):
    """Test get user endpoint when not authenticated."""
    response = await client.get("/api/user")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"

@pytest.mark.asyncio
async def test_get_dashboard_authenticated(client, authenticated_session, mock_ai_service):
    """Test get dashboard endpoint when authenticated."""
    authenticated_session["user"]["org_id"] = "org-123"
    mock_ai_service.get_greeting.return_value = "Custom AI Greeting"
    
    response = await client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == authenticated_session["user"]["email"]
    assert data["org_id"] == "org-123"
    assert data["message"] == "Custom AI Greeting"

@pytest.mark.asyncio
async def test_get_dashboard_ai_failure(client, authenticated_session, mock_ai_service):
    """Test get dashboard endpoint when AI service fails."""
    mock_ai_service.get_greeting.side_effect = Exception("AI Error")
    
    response = await client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Welcome back! You are successfully authenticated."

@pytest.mark.asyncio
async def test_get_dashboard_ai_greeting_disabled(client, authenticated_session):
    """Test dashboard endpoint when AI greeting is feature-flagged off."""
    authenticated_session["user"]["org_id"] = "org-456"
    with patch("app.routes.user_routes.settings.ENABLE_AI_GREETING", False):
        response = await client.get("/api/dashboard")
        assert response.status_code == 200
        assert response.json()["org_id"] == "org-456"
        assert response.json()["message"] == "Welcome back! You are successfully authenticated."

@pytest.mark.asyncio
async def test_get_admin_overview(client, authenticated_session):
    """Test the admin overview derives its organisation from the authenticated session."""
    authenticated_session["user"]["org_id"] = "org-admin"
    authenticated_session["user"]["roles"] = ["admin"]
    with patch("app.routes.user_routes.httpx.get") as mock_get, patch(
        "app.routes.user_routes.settings.ORG_DB_CONNECTIONS",
        {"org-admin": "postgres://admin", "org-other": "postgres://other"},
    ):
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: {"data": {"result": [{"value": [0, "7"]}]}}),
            MagicMock(status_code=200, json=lambda: {"data": {"result": [{"value": [0, "23"]}]}}),
        ]
        response = await client.get("/api/admin/overview?org_id=org-other")
        assert response.status_code == 200
        body = response.json()
        assert body["org_id"] == "org-admin"
        assert body["organizations"] == ["org-admin"]
        assert "db_connections" not in body
        assert body["usage"]["queries_total"] == 7
        assert all("org-admin" in call.args[0] for call in mock_get.call_args_list)


@pytest.mark.asyncio
async def test_get_admin_overview_rejects_missing_org_claim(client, authenticated_session):
    """An admin session without an organisation cannot access tenant data."""
    authenticated_session["user"]["roles"] = ["admin"]

    response = await client.get("/api/admin/overview?org_id=org-other")

    assert response.status_code == 403
    assert response.json()["detail"] == "Organisation claim required"


@pytest.mark.asyncio
async def test_get_admin_overview_rejects_non_admin(client, authenticated_session):
    """Only an authenticated admin may access the admin overview."""
    authenticated_session["user"]["org_id"] = "org-admin"
    authenticated_session["user"]["roles"] = ["viewer"]

    response = await client.get("/api/admin/overview")

    assert response.status_code == 403
    assert response.json()["detail"] == "Administrator role required"

@pytest.mark.asyncio
async def test_text_to_sql_authenticated(client, authenticated_session, mock_ai_service):
    """Test text-to-sql endpoint when authenticated."""
    # Mock TextToSqlService.generate_sql_from_text indirectly via dependencies
    mock_result = {
        "sql": "SELECT * FROM users",
        "schema": "test schema"
    }
    
    with patch("app.routes.user_routes.TextToSqlService") as mock_service_class:
        mock_service = mock_service_class.return_value
        mock_service.generate_sql_from_text = AsyncMock(return_value=mock_result)
        
        response = await client.post("/api/text-to-sql", json={"query": "get all users"})
        assert response.status_code == 200
        data = response.json()
        assert data["sql"] == "SELECT * FROM users"

@pytest.mark.asyncio
async def test_text_to_sql_validation_error(client, authenticated_session, mock_ai_service):
    """Test text-to-sql endpoint with empty query."""
    response = await client.post("/api/text-to-sql", json={"query": ""})
    assert response.status_code == 400
    assert "Query cannot be empty" in response.json()["detail"]
