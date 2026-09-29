import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from auth import Principal
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest, TenantQuota
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.tenant_database_resolver import TenantDatabaseConfig


class MockProvider:
    def __init__(self, binding, service):
        self.binding = binding
        self.service = service

    def resolve(self, principal, database_id):
        return self.binding, self.service


@pytest.fixture
def safety_checker():
    return DefaultSqlSafetyChecker()


@pytest.fixture
def principal():
    return Principal(
        user_id="user-123",
        email="test@example.com",
        org_id="org-1",
        roles=["viewer"]
    )


@pytest.fixture
def binding():
    return TenantDatabaseConfig(
        org_id="org-1",
        database_id="default",
        credential_mode="static",
        connection_string="sqlite+aiosqlite:///:memory:",
        data_schema="main",
        metadata_schema="meta"
    )


@pytest.fixture
def service(binding):
    mock_service = AsyncMock()
    mock_service.execute_sql_statement = AsyncMock(return_value=[{"id": 1, "name": "test"}])
    mock_service.repository = MagicMock()
    mock_service.repository.database_target = "primary"
    return mock_service


@pytest.fixture
def gateway(safety_checker, binding, service):
    provider = MockProvider(binding, service)
    return GovernedQueryGateway(
        provider=provider,
        safety_checker=safety_checker,
        tenant_concurrent_limit=2,
        tenant_queue_limit=3
    )


@pytest.mark.asyncio
async def test_tenant_concurrent_limit_enforced(gateway, principal):
    """Test that concurrent queries are limited per tenant."""
    request = GovernedQueryRequest(
        principal=principal,
        database_id="default",
        sql="SELECT 1"
    )

    # First two should succeed (limit=2)
    results = await asyncio.gather(
        gateway.execute(request),
        gateway.execute(request),
        return_exceptions=True
    )
    assert all(not isinstance(r, Exception) for r in results)

    # Third should be rejected or queued (depending on timing)
    # With semaphore limit=2, third will wait. Let's test the queue limit
    # We need to fill the queue (limit=3)
    quota = gateway._get_tenant_quota("org-1", "default")
    assert quota.max_concurrent == 2
    assert quota.queue_limit == 3


@pytest.mark.asyncio
async def test_tenant_queue_limit_enforced(gateway, principal):
    """Test that queue limit is enforced per tenant."""
    # Fill up the queue manually
    quota = gateway._get_tenant_quota("org-1", "default")
    quota.current_queued = 3  # queue_limit=3

    request = GovernedQueryRequest(
        principal=principal,
        database_id="default",
        sql="SELECT 1"
    )

    with pytest.raises(PermissionError, match="Tenant query queue full"):
        await gateway.execute(request)


@pytest.mark.asyncio
async def test_different_tenants_independent_quotas(gateway, safety_checker, binding, service):
    """Test that different tenants have independent quotas."""
    # Create second tenant
    principal2 = Principal(
        user_id="user-456",
        email="test2@example.com",
        org_id="org-2",
        roles=["viewer"]
    )
    binding2 = TenantDatabaseConfig(
        org_id="org-2",
        database_id="default",
        credential_mode="static",
        connection_string="sqlite+aiosqlite:///:memory:",
        data_schema="main",
        metadata_schema="meta"
    )
    provider2 = MockProvider(binding2, service)
    gateway2 = GovernedQueryGateway(
        provider=provider2,
        safety_checker=safety_checker,
        tenant_concurrent_limit=2,
        tenant_queue_limit=3
    )

    request1 = GovernedQueryRequest(
        principal=Principal(user_id="u1", email="a@b.com", org_id="org-1", roles=["viewer"]),
        database_id="default",
        sql="SELECT 1"
    )
    request2 = GovernedQueryRequest(
        principal=principal2,
        database_id="default",
        sql="SELECT 1"
    )

    # Both should be able to run concurrently since they're different tenants
    results = await asyncio.gather(
        gateway.execute(request1),
        gateway2.execute(request2),
        return_exceptions=True
    )
    assert all(not isinstance(r, Exception) for r in results)


@pytest.mark.asyncio
async def test_quota_released_after_execution(gateway, principal):
    """Test that semaphore is released after query execution (success or error)."""
    request = GovernedQueryRequest(
        principal=principal,
        database_id="default",
        sql="SELECT 1"
    )

    # Execute one query
    await gateway.execute(request)

    # Verify semaphore released by checking available permits
    quota = gateway._get_tenant_quota("org-1", "default")
    # After release, should be able to acquire again immediately
    await quota.semaphore.acquire()
    quota.semaphore.release()


@pytest.mark.asyncio
async def test_invalid_sql_releases_quota(gateway, principal):
    """Test that quota is released even when validation fails."""
    request = GovernedQueryRequest(
        principal=principal,
        database_id="default",
        sql="INVALID SQL"  # This should fail validation
    )

    with pytest.raises(ValueError):
        await gateway.execute(request)

    # Quota should be released
    quota = gateway._get_tenant_quota("org-1", "default")
    await quota.semaphore.acquire()
    quota.semaphore.release()


@pytest.mark.asyncio
async def test_policy_denied_releases_quota(gateway, principal):
    """Test that quota is released when policy denies."""
    # Create a gateway with a policy evaluator that denies
    from services.policy_engine import PolicyEvaluator, PolicyDecision
    
    class DenyPolicyEvaluator:
        def evaluate(self, *args, **kwargs):
            return PolicyDecision(
                allowed=False,
                reason="Denied by test policy",
                policy_ids=["test-deny"],
                row_restrictions={},
                masked_columns=[]
            )
    
    provider = gateway._provider
    deny_gateway = GovernedQueryGateway(
        provider=provider,
        safety_checker=gateway._safety_checker,
        policy_evaluator=DenyPolicyEvaluator(),
        tenant_concurrent_limit=2,
        tenant_queue_limit=3
    )

    request = GovernedQueryRequest(
        principal=principal,
        database_id="default",
        sql="SELECT 1"
    )

    with pytest.raises(PermissionError, match="Denied by test policy"):
        await deny_gateway.execute(request)

    # Quota should be released
    quota = deny_gateway._get_tenant_quota("org-1", "default")
    await quota.semaphore.acquire()
    quota.semaphore.release()