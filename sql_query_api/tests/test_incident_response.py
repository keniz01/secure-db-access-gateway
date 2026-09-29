"""Tests for incident response module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys

# Mock redis before importing the module
sys.modules['redis'] = MagicMock()
sys.modules['redis.asyncio'] = MagicMock()

from services.incident_response import (
    IncidentResponseManager,
    IncidentResponseConfig,
    BlockedIP,
    RevokedSession,
)


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.setex = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.exists = AsyncMock(return_value=0)
    mock.keys = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def incident_manager(mock_redis):
    """Create IncidentResponseManager with mocked Redis."""
    config = IncidentResponseConfig(redis_url="redis://localhost:6379/0")
    manager = IncidentResponseManager(config)
    manager._redis = mock_redis
    return manager


@pytest.mark.asyncio
async def test_block_ip_new(incident_manager, mock_redis):
    """Test blocking a new IP."""
    result = await incident_manager.block_ip("192.168.1.100", "brute force", "admin")
    assert result is True
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_block_ip_already_blocked(incident_manager, mock_redis):
    """Test blocking an already blocked IP."""
    mock_redis.get = AsyncMock(return_value="already blocked")
    result = await incident_manager.block_ip("192.168.1.100", "brute force", "admin")
    assert result is False


@pytest.mark.asyncio
async def test_unblock_ip(incident_manager, mock_redis):
    """Test unblocking an IP."""
    mock_redis.delete = AsyncMock(return_value=1)
    result = await incident_manager.unblock_ip("192.168.1.100", "admin")
    assert result is True
    mock_redis.delete.assert_called_once_with("security:blocklist:ip:192.168.1.100")


@pytest.mark.asyncio
async def test_unblock_ip_not_blocked(incident_manager, mock_redis):
    """Test unblocking an IP that wasn't blocked."""
    mock_redis.delete = AsyncMock(return_value=0)
    result = await incident_manager.unblock_ip("192.168.1.100", "admin")
    assert result is False


@pytest.mark.asyncio
async def test_is_ip_blocked(incident_manager, mock_redis):
    """Test checking if IP is blocked."""
    mock_redis.exists = AsyncMock(return_value=1)
    result = await incident_manager.is_ip_blocked("192.168.1.100")
    assert result is True


@pytest.mark.asyncio
async def test_revoke_session(incident_manager, mock_redis):
    """Test revoking a session."""
    mock_redis.setex = AsyncMock(return_value=True)
    result = await incident_manager.revoke_session("sess_123", "user_456", "compromise", "admin")
    assert result is True
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_is_session_revoked(incident_manager, mock_redis):
    """Test checking if session is revoked."""
    mock_redis.exists = AsyncMock(return_value=1)
    result = await incident_manager.is_session_revoked("sess_123")
    assert result is True


@pytest.mark.asyncio
async def test_isolate_tenant(incident_manager, mock_redis):
    """Test isolating a tenant."""
    mock_redis.set = AsyncMock(return_value=True)
    result = await incident_manager.isolate_tenant("org_123", "compromise", "admin")
    assert result is True
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_restore_tenant(incident_manager, mock_redis):
    """Test restoring a tenant."""
    mock_redis.delete = AsyncMock(return_value=1)
    result = await incident_manager.restore_tenant("org_123", "admin")
    assert result is True
    mock_redis.delete.assert_called_once_with("security:tenant_isolation:org_123")


@pytest.mark.asyncio
async def test_restore_tenant_not_isolated(incident_manager, mock_redis):
    """Test restoring a tenant that wasn't isolated."""
    mock_redis.delete = AsyncMock(return_value=0)
    result = await incident_manager.restore_tenant("org_123", "admin")
    assert result is False


@pytest.mark.asyncio
async def test_is_tenant_isolated(incident_manager, mock_redis):
    """Test checking if tenant is isolated."""
    mock_redis.exists = AsyncMock(return_value=1)
    result = await incident_manager.is_tenant_isolated("org_123")
    assert result is True


@pytest.mark.asyncio
async def test_revoke_all_user_sessions(incident_manager, mock_redis):
    """Test revoking all user sessions."""
    mock_redis.keys = AsyncMock(return_value=["key1", "key2", "key3"])
    mock_redis.setex = AsyncMock(return_value=True)
    count = await incident_manager.revoke_all_user_sessions("user_123", "compromise", "admin")
    assert count == 3
    assert mock_redis.setex.call_count == 3


@pytest.mark.asyncio
async def test_config_from_environment():
    """Test config loading from environment."""
    config = IncidentResponseConfig()
    # Default values
    assert config.redis_url == "redis://redis:6379/0"
    assert config.session_ttl == 1800
    assert config.block_duration == 3600
    assert config.blocklist_key == "security:blocklist:ip"
    assert config.revoked_sessions_key == "security:revoked_sessions"
    assert config.tenant_isolation_key == "security:tenant_isolation"