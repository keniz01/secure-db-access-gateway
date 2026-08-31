from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse

from auth import build_principal_from_claims, extract_bearer_token, validate_access_token


class RBACMiddleware:
    """Authenticate via Auth0 JWT claims and ignore spoofed caller-provided headers."""

    ALLOWED_ROLES = {"viewer", "admin"}
    SPOOFABLE_HEADERS = {
        "x-user-email",
        "x-user-id",
        "x-user-role",
        "x-org-id",
        "x-tenant-id",
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        request.state.user = None
        request.state.user_email = None
        request.state.user_org_id = None
        request.state.user_role = "viewer"

        if request.url.path.startswith("/graphql"):
            if any(key.lower() in self.SPOOFABLE_HEADERS for key in request.headers.keys()):
                token = extract_bearer_token(request)
                if not token:
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "Authentication required."},
                    )
                    await response(scope, receive, send)
                    return

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

            request.state.user = principal
            request.state.user_email = principal.get("email")
            request.state.user_org_id = principal.get("org_id")
            request.state.user_role = principal.get("role", "viewer")

            if request.state.user_role not in self.ALLOWED_ROLES:
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden: user role is not authorized to access this API."},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
