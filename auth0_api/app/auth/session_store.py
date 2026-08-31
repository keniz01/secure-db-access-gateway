"""Server-side storage for opaque browser sessions.

Replace this in-memory implementation with a shared store (for example Redis) before
running more than one Auth0 API process.
"""

import secrets
import time
from typing import Any

_sessions: dict[str, dict[str, Any]] = {}


def create_session(user: dict[str, Any], access_token: str, ttl_seconds: int) -> str:
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {"user": user, "access_token": access_token, "expires_at": time.time() + ttl_seconds}
    return session_id


def get_session(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    session = _sessions.get(session_id)
    if not session or session["expires_at"] <= time.time():
        _sessions.pop(session_id, None)
        return None
    return session


def revoke_session(session_id: str | None) -> None:
    if session_id:
        _sessions.pop(session_id, None)
