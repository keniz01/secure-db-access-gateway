from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse

from auth import build_principal_from_claims, extract_bearer_token, validate_access_token


class RBACMiddleware:
    """Authenticate via Auth0 JWT claims and ignore spoofed caller-provided headers."""

    ALLOWED_ROLES = {"viewer", "admin"}
    SPOOFABLE_HEADER_PREFIXES = (b"x-user-", b"x-org-", b"x-tenant-")

    def __init__(self, app):
        self.app = app

    @classmethod
    def _strip_spoofable_headers(cls, scope) -> None:
        """Remove caller identity metadata before any downstream handler can read it."""
        scope["headers"] = [
            (name, value)
            for name, value in scope.get("headers", [])
            if not name.lower().startswith(cls.SPOOFABLE_HEADER_PREFIXES)
        ]

    async def __call__(self, scope, receive, send):
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
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required."},
                )
                await response(scope, receive, send)
                return

            principal = build_principal_from_claims(claims)
            if principal is None:
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required."},
                )
                await response(scope, receive, send)
                return

            request.state.principal = principal

            if principal.role not in self.ALLOWED_ROLES:
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden: user role is not authorized to access this API."},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
