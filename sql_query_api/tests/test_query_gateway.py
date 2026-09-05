from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import Principal
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import TenantDatabaseConfig


@pytest.mark.asyncio
async def test_gateway_runs_validation_execution_and_audit_in_one_path() -> None:
    principal = Principal("user-1", "user@example.com", "org-1", frozenset({"viewer"}))
    binding = TenantDatabaseConfig("org-1", "analytics", "sqlite+aiosqlite:///:memory:")
    service = MagicMock()
    service.repository.database_target = "primary"
    service.execute_sql_statement = AsyncMock(return_value=[{"value": 1}])
    provider = MagicMock()
    provider.resolve.return_value = (binding, service)
    audit = MagicMock()

    gateway = GovernedQueryGateway(provider, DefaultSqlSafetyChecker(), audit=audit)
    result = await gateway.execute(
        GovernedQueryRequest(
            principal=principal,
            database_id="analytics",
            sql="SELECT value FROM facts",
        )
    )

    assert result == [{"value": 1}]
    provider.resolve.assert_called_once_with(principal, "analytics")
    service.execute_sql_statement.assert_awaited_once_with(
        "SELECT value FROM facts", None
    )
    audit.assert_called_once()
    assert audit.call_args.args == ("sql_query",)
    assert audit.call_args.kwargs["org_id"] == "org-1"


@pytest.mark.asyncio
async def test_gateway_rejects_mutation_before_resolving_or_executing() -> None:
    principal = Principal("user-1", "user@example.com", "org-1", frozenset({"viewer"}))
    provider = MagicMock()
    service = MagicMock()
    service.execute_sql_statement = AsyncMock()
    provider.resolve.return_value = (
        TenantDatabaseConfig("org-1", "analytics", "sqlite+aiosqlite:///:memory:"),
        service,
    )
    gateway = GovernedQueryGateway(provider, DefaultSqlSafetyChecker())

    with pytest.raises(ValueError, match="safety validation"):
        await gateway.execute(
            GovernedQueryRequest(
                principal=principal,
                database_id="analytics",
                sql="DELETE FROM facts",
            )
        )

    service.execute_sql_statement.assert_not_awaited()
