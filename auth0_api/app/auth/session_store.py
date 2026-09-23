"""Production-ready server-side session store.

- Production: Redis is mandatory. `REDIS_URL` must be set (e.g. `redis://redis:6379/0`);
  startup fails fast otherwise (`validate_session_store`).
- Non-production (dev/test/CI): if `REDIS_URL` is unset, falls back to in-memory dict
  so `pytest` remains hermetic without external services. When `REDIS_URL` is set,
  Redis is used even in dev (Docker Compose provides it).

Sessions are JSON-serialized with `SETEX ttl`; deserialization restores dict.
Memory fallback stores `expires_at` and lazily expires on read.
"""

import json
import os
import secrets
import time
from typing import Any

_REDIS_URL_ENV = "REDIS_URL"

_sessions: dict[str, dict[str, Any]] = {}
_redis_client: Any | None = None
_redis_url_cached: str | None = None


def _get_redis_url() -> str:
    return os.getenv(_REDIS_URL_ENV, "").strip()


def _is_production() -> bool:
    try:
        from shared_secrets import is_environment_production

        return is_environment_production()
    except Exception:
        return os.getenv("ENVIRONMENT", "production").lower() in {"production", "prod"}


def _get_redis() -> Any | None:
    """Lazily create async Redis client if REDIS_URL is configured."""
    global _redis_client, _redis_url_cached
    url = _get_redis_url()
    if not url:
        return None
    if _redis_client is not None and _redis_url_cached == url:
        return _redis_client
    try:
        import redis.asyncio as redis  # type: ignore[import-not-found]

        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_url_cached = url
        return _redis_client
    except ImportError as exc:
        raise RuntimeError(
            "redis-py is required when REDIS_URL is set. Install with `pip install redis>=5`"
        ) from exc


def _redis_key(session_id: str) -> str:
    return f"gateway:session:{session_id}"


def validate_session_store() -> None:
    """Fail fast if production is misconfigured.

    Production requires REDIS_URL for shared, horizontally-scalable sessions.
    Non-production may use in-memory fallback but will use Redis when available.
    """
    url = _get_redis_url()
    if _is_production() and not url:
        raise RuntimeError(
            "REDIS_URL is required in production for shared session storage. "
            "Set REDIS_URL=redis://redis:6379/0 (Docker Compose provides `redis` service) "
            "or set ENVIRONMENT=dev for single-process local dev."
        )
    # Eagerly validate Redis import when URL is set
    if url:
        _get_redis()


async def create_session(user: dict[str, Any], access_token: str, ttl_seconds: int) -> str:
    session_id = secrets.token_urlsafe(32)
    payload: dict[str, Any] = {
        "user": user,
        "access_token": access_token,
        "csrf_token": secrets.token_urlsafe(32),
        "expires_at": time.time() + ttl_seconds,
    }
    client = _get_redis()
    if client is not None:
        await client.setex(_redis_key(session_id), ttl_seconds, json.dumps(payload))
    else:
        # In-memory fallback (non-production without Redis)
        _sessions[session_id] = payload
    return session_id


async def get_session(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    client = _get_redis()
    if client is not None:
        raw = await client.get(_redis_key(session_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await client.delete(_redis_key(session_id))
            return None
        # Redis TTL already enforces expiry; still honor expires_at if present
        if isinstance(data.get("expires_at"), (int, float)) and data["expires_at"] <= time.time():
            await client.delete(_redis_key(session_id))
            return None
        return data
    # In-memory path
    session = _sessions.get(session_id)
    if not session or session.get("expires_at", 0) <= time.time():
        _sessions.pop(session_id, None)
        return None
    return session


async def revoke_session(session_id: str | None) -> None:
    if not session_id:
        return
    client = _get_redis()
    if client is not None:
        await client.delete(_redis_key(session_id))
    # Always clear memory copy (covers fallback and any stale local copy)
    _sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# Test helpers (sync) - not for production use
# ---------------------------------------------------------------------------

def _clear_memory_store() -> None:
    _sessions.clear()
