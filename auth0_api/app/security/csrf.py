"""Cross-site request forgery defenses for browser state-changing calls.

The browser-facing Auth0 API authenticates with an opaque session cookie that
browsers attach to requests automatically. A cookie alone does not prove a
request is legitimate, so every state-changing (POST) request must clear
several independent layers:

1. SameSite=lax cookies: browsers refuse to attach the session cookie to
   cross-site POST submissions in the first place.
2. Origin/Referer allowlist: every state-changing request must present an
   Origin (or, when absent, a Referer) that resolves to a trusted origin.
   Browsers always send an Origin header on POSTs, so a request carrying the
   session cookie but no usable Origin/Referer is rejected.
3. Browser marker: the SPA sends ``X-Requested-With: XMLHttpRequest`` on every
   request; a cross-site form submission cannot set it.
4. Double-submit CSRF token: the server stores a random token in the
   server-side session and mirrors it in a ``csrf_token`` cookie. The SPA must
   echo the token in an ``X-CSRF-Token`` header, and the server requires
   cookie == header == server-side session token.

Server-to-server callers authenticate with a bearer token and never send
cookies, so they are not subject to these gates.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import Request, Response

from app.config.settings import settings
from app.utils.helpers import normalize_origin

#: Cookie the server stamps with the double-submit token. Not HttpOnly on
#: purpose: the SPA must be able to read it back and echo it in a header.
CSRF_COOKIE_NAME = "csrf_token"
#: Header the SPA uses to echo the double-submit token.
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FAILURE_DETAIL = "CSRF protection failed"


def get_allowed_origins() -> list[str]:
    """Return the canonicalised origin allowlist for CORS and Origin/Referer checks.

    A single source of truth prevents CORS and CSRF origin validation from
    drifting apart. Origins are normalised to ``scheme://host[:port]``.
    """
    allowed: list[str] = []
    for origin in settings.ALLOWED_ORIGINS or []:
        if not origin:
            continue
        normalized = normalize_origin(str(origin).strip())
        if normalized and normalized not in allowed:
            allowed.append(normalized)
    # The login flow derives the frontend origin from these; keep them in the
    # allowlist so a valid login origin is also a valid Origin for state changes.
    for url in (settings.REACT_APP_URL, settings.FRONTEND_URL):
        if url:
            normalized = normalize_origin(str(url).strip())
            if normalized and normalized not in allowed:
                allowed.append(normalized)
    return allowed


def _origin_from_header(value: str | None) -> str | None:
    """Extract a normalised origin from an Origin/Referer header value.

    Returns ``None`` for missing headers and for the literal ``null`` origin
    that sandboxed iframes produce (never trusted).
    """
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() == "null":
        return None
    return normalize_origin(value)


def request_origin_is_allowed(request: Request) -> bool:
    """Return True when the request's Origin (or falling-back Referer) is trusted.

    Only the browser emits these headers; a server-to-server client that sends
    cookies is indistinguishable from a browser and must also pass this check.
    """
    allowed = get_allowed_origins()
    origin = _origin_from_header(request.headers.get("Origin"))
    if origin is not None:
        return origin in allowed
    referer = _origin_from_header(request.headers.get("Referer"))
    return referer is not None and referer in allowed


def csrf_failure_reason(request: Request, session: dict[str, Any] | None) -> str | None:
    """Return a failure detail string when the request is not CSRF-safe.

    Returns ``None`` when all defense layers pass. ``session`` is the resolved
    server-side session (must carry the ``csrf_token`` created at login).
    """
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        return CSRF_FAILURE_DETAIL
    if not request_origin_is_allowed(request):
        return CSRF_FAILURE_DETAIL

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token:
        return CSRF_FAILURE_DETAIL
    if not secrets.compare_digest(str(cookie_token).encode(), str(header_token).encode()):
        return CSRF_FAILURE_DETAIL

    session_token = (session or {}).get("csrf_token")
    if session_token is None or not secrets.compare_digest(
        str(cookie_token).encode(), str(session_token).encode()
    ):
        return CSRF_FAILURE_DETAIL
    return None


def set_csrf_cookie(response: Response, session: dict[str, Any] | None) -> None:
    """Stamp the response with the double-submit ``csrf_token`` cookie.

    Written next to the session cookie whenever a session is created so the SPA
    can read it for the header echo. SameSite/secure flags mirror the session
    cookie; the value is additionally bound to the server-side session.
    """
    token = (session or {}).get("csrf_token")
    if not token:
        return
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=str(token),
        max_age=settings.SESSION_MAX_AGE,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=False,
        samesite="lax",
    )


def clear_csrf_cookie(response: Response) -> None:
    """Expire the double-submit cookie (used on logout)."""
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="lax",
    )