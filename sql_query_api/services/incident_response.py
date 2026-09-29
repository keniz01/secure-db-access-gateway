"""Incident response hooks for security events.

Provides automated response capabilities for security incidents:
- Session revocation
- IP blocking
- Emergency tenant isolation
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import redis.asyncio as redis
from loguru import logger

from config.app_logger import log_audit_event


@dataclass(slots=True)
class IncidentResponseConfig:
    """Configuration for incident response."""
    redis_url: str = "redis://redis:6379/0"
    session_ttl: int = 1800  # 30 minutes
    block_duration: int = 3600  # 1 hour
    blocklist_key: str = "security:blocklist:ip"
    revoked_sessions_key: str = "security:revoked_sessions"
    tenant_isolation_key: str = "security:tenant_isolation"


@dataclass(slots=True)
class BlockedIP:
    """Record of a blocked IP address."""
    ip: str
    reason: str
    blocked_at: float
    expires_at: float
    blocked_by: str


@dataclass(slots=True)
class RevokedSession:
    """Record of a revoked session."""
    session_id: str
    user_id: str
    reason: str
    revoked_at: float
    revoked_by: str


class IncidentResponseManager:
    """Manages automated incident response actions."""

    def __init__(self, config: IncidentResponseConfig | None = None) -> None:
        self._config = config or IncidentResponseConfig(
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        )
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis client."""
        if self._redis is None:
            self._redis = redis.from_url(
                self._config.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    # --- IP Blocking ---

    async def block_ip(self, ip: str, reason: str, blocked_by: str, duration: int | None = None) -> bool:
        """Block an IP address for a specified duration.

        Args:
            ip: IP address to block
            reason: Reason for blocking
            blocked_by: Identity of the blocker (user or system)
            duration: Block duration in seconds (default from config)

        Returns:
            True if IP was newly blocked, False if already blocked
        """
        r = await self._get_redis()
        duration = duration or self._config.block_duration
        expires_at = time.time() + duration
        key = f"{self._config.blocklist_key}:{ip}"

        # Check if already blocked
        existing = await r.get(key)
        if existing:
            logger.warning("IP {} already blocked", ip)
            return False

        blocked = BlockedIP(
            ip=ip,
            reason=reason,
            blocked_at=time.time(),
            expires_at=expires_at,
            blocked_by=blocked_by,
        )

        await r.setex(key, duration, blocked.__repr__())
        log_audit_event("ip_blocked", ip=ip, reason=reason, blocked_by=blocked_by,
                        duration=duration, expires_at=expires_at)
        logger.warning("Blocked IP {} for {}: {}", ip, duration, reason)
        return True

    async def unblock_ip(self, ip: str, unblocked_by: str) -> bool:
        """Unblock an IP address.

        Args:
            ip: IP address to unblock
            unblocked_by: Identity of the unblocker

        Returns:
            True if IP was blocked and now unblocked, False if not blocked
        """
        r = await self._get_redis()
        key = f"{self._config.blocklist_key}:{ip}"
        result = await r.delete(key)
        if result:
            log_audit_event("ip_unblocked", ip=ip, unblocked_by=unblocked_by)
            logger.info("Unblocked IP {} by {}", ip, unblocked_by)
            return True
        return False

    async def is_ip_blocked(self, ip: str) -> bool:
        """Check if an IP is currently blocked."""
        r = await self._get_redis()
        key = f"{self._config.blocklist_key}:{ip}"
        return await r.exists(key) > 0

    async def get_blocked_ips(self) -> list[dict[str, Any]]:
        """Get list of currently blocked IPs."""
        r = await self._get_redis()
        keys = await r.keys(f"{self._config.blocklist_key}:*")
        blocked = []
        for key in keys:
            value = await r.get(key)
            ttl = await r.ttl(key)
            if value:
                blocked.append({
                    "ip": key.replace(f"{self._config.blocklist_key}:", ""),
                    "value": value,
                    "ttl_seconds": ttl,
                })
        return blocked

    # --- Session Revocation ---

    async def revoke_session(self, session_id: str, user_id: str, reason: str, revoked_by: str) -> bool:
        """Revoke a user session.

        Args:
            session_id: Session ID to revoke
            user_id: User ID whose session is being revoked
            reason: Reason for revocation
            revoked_by: Identity of the revoker

        Returns:
            True if session was revoked
        """
        r = await self._get_redis()
        key = f"{self._config.revoked_sessions_key}:{session_id}"
        expires_at = time.time() + self._config.session_ttl

        revoked = RevokedSession(
            session_id=session_id,
            user_id=user_id,
            reason=reason,
            revoked_at=time.time(),
            revoked_by=revoked_by,
        )

        await r.setex(key, self._config.session_ttl, revoked.__repr__())
        log_audit_event("session_revoked", session_id=session_id, user_id=user_id,
                        reason=reason, revoked_by=revoked_by)
        logger.warning("Revoked session {} for user {}: {}", session_id, user_id, reason)
        return True

    async def is_session_revoked(self, session_id: str) -> bool:
        """Check if a session has been revoked."""
        r = await self._get_redis()
        key = f"{self._config.revoked_sessions_key}:{session_id}"
        return await r.exists(key) > 0

    async def revoke_all_user_sessions(self, user_id: str, reason: str, revoked_by: str) -> int:
        """Revoke all sessions for a user (by pattern matching).

        Note: This requires session IDs to be stored with user_id prefix.
        """
        r = await self._get_redis()
        # This is a simplified implementation - in production you'd maintain
        # a user->sessions index
        pattern = f"{self._config.revoked_sessions_key}:{user_id}:*"
        keys = await r.keys(pattern)
        count = 0
        for key in keys:
            await r.setex(key, self._config.session_ttl, f"revoked:{reason}:{revoked_by}")
            count += 1
        if count > 0:
            log_audit_event("user_sessions_revoked", user_id=user_id, count=count,
                            reason=reason, revoked_by=revoked_by)
        return count

    # --- Emergency Tenant Isolation ---

    async def isolate_tenant(self, org_id: str, reason: str, isolated_by: str) -> bool:
        """Emergency isolation of a tenant - blocks all access for the org.

        This sets a flag that RBAC middleware checks to deny all requests
        for the specified organization.
        """
        r = await self._get_redis()
        key = f"{self._config.tenant_isolation_key}:{org_id}"
        value = f"{reason}:{isolated_by}:{time.time()}"
        await r.set(key, value)
        log_audit_event("tenant_isolated", org_id=org_id, reason=reason, isolated_by=isolated_by)
        logger.critical("EMERGENCY: Tenant {} isolated by {}: {}", org_id, isolated_by, reason)
        return True

    async def restore_tenant(self, org_id: str, restored_by: str) -> bool:
        """Restore tenant access after emergency isolation."""
        r = await self._get_redis()
        key = f"{self._config.tenant_isolation_key}:{org_id}"
        result = await r.delete(key)
        if result:
            log_audit_event("tenant_restored", org_id=org_id, restored_by=restored_by)
            logger.info("Tenant {} restored by {}", org_id, restored_by)
            return True
        return False

    async def is_tenant_isolated(self, org_id: str) -> bool:
        """Check if a tenant is isolated."""
        r = await self._get_redis()
        key = f"{self._config.tenant_isolation_key}:{org_id}"
        return await r.exists(key) > 0


# Global instance
_incident_response: IncidentResponseManager | None = None


def get_incident_response() -> IncidentResponseManager:
    """Get the global incident response manager."""
    global _incident_response
    if _incident_response is None:
        _incident_response = IncidentResponseManager()
    return _incident_response


# Convenience functions for common operations
async def block_ip(ip: str, reason: str, blocked_by: str = "system", duration: int | None = None) -> bool:
    """Block an IP address."""
    return await get_incident_response().block_ip(ip, reason, blocked_by, duration)


async def unblock_ip(ip: str, unblocked_by: str = "system") -> bool:
    """Unblock an IP address."""
    return await get_incident_response().unblock_ip(ip, unblocked_by)


async def revoke_session(session_id: str, user_id: str, reason: str, revoked_by: str = "system") -> bool:
    """Revoke a session."""
    return await get_incident_response().revoke_session(session_id, user_id, reason, revoked_by)


async def revoke_user_sessions(user_id: str, reason: str, revoked_by: str = "system") -> int:
    """Revoke all sessions for a user."""
    return await get_incident_response().revoke_all_user_sessions(user_id, reason, revoked_by)


async def isolate_tenant(org_id: str, reason: str, isolated_by: str = "system") -> bool:
    """Emergency isolate a tenant."""
    return await get_incident_response().isolate_tenant(org_id, reason, isolated_by)


async def restore_tenant(org_id: str, restored_by: str = "system") -> bool:
    """Restore tenant after isolation."""
    return await get_incident_response().restore_tenant(org_id, restored_by)


async def is_tenant_isolated(org_id: str) -> bool:
    """Check if tenant is isolated."""
    return await get_incident_response().is_tenant_isolated(org_id)