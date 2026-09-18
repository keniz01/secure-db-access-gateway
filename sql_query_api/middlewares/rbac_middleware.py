from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from auth import build_principal_from_claims, extract_bearer_token, validate_access_token
from config.app_logger import log_audit_event


class RBACMiddleware:
    """Authenticate via Auth0 JWT claims and ignore spoofed caller-provided headers."""

    ALLOWED_ROLES = {"viewer", "admin"}
    SPOOFABLE_HEADER_PREFIXES = (b"x-user-", b"x-org-", b"x-tenant-")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @classmethod
    def _strip_spoofable_headers(cls, scope: Scope) -> None:
        """Remove caller identity metadata before any downstream handler can read it."""
        scope["headers"] = [
            (name, value)
            for name, value in scope.get("headers", [])
            if not name.lower().startswith(cls.SPOOFABLE_HEADER_PREFIXES)
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Authenticate and authorize GraphQL requests from trusted JWT claims."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        self._strip_spoofable_headers(scope)
        request = Request(scope, receive=receive)
        request.state.principal = None

        if request.url.path.startswith("/graphql"):
            token = extract_bearer_token(request)
            claims: dict[str, Any] | None = validate_access_token(token) if token else None
            if claims is None:
                log_audit_event("auth_failed", reason="missing_or_invalid_bearer_token", path=request.url.path)
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required."},
                )
                await response(scope, receive, send)
                return

            principal = build_principal_from_claims(claims)
            if principal is None:
                log_audit_event("auth_failed", reason="principal_from_claims_failed", path=request.url.path)
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required."},
                )
                await response(scope, receive, send)
                return

            request.state.principal = principal

            if principal.role not in self.ALLOWED_ROLES:
                log_audit_event("auth_failed", reason="unauthorized_role", role=principal.role, path=request.url.path)
                response = JSONResponse(
                    status_code=40,
                    content={"detail": "Forbidden: user role is not authorized to access this API."},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
