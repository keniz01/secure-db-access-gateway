from fastapi import Request
from starlette.responses import JSONResponse


class RBACMiddleware:
    """Minimal role-based access control for the GraphQL API."""

    ALLOWED_ROLES = {"viewer", "admin"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if request.url.path.startswith("/graphql"):
            role = (request.headers.get("x-user-role") or "viewer").lower()
            if role not in self.ALLOWED_ROLES:
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden: user role is not authorized to access this API."},
                )
                await response(scope, receive, send)
                return

            request.state.user_role = role

        await self.app(scope, receive, send)
