import asyncio
import os
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RateLimitMiddleware:
    """
    Simple in-memory rate limiting for high-volume GraphQL requests.

    Budgets are keyed by the authenticated principal (the trusted JWT subject)
    set by the outer RBAC middleware, so the limiter keeps working when all
    traffic funnels through a single reverse-proxy IP (the production BFF).
    ``X-Forwarded-For`` is only trusted when ``TRUST_PROXY=1`` is configured;
    otherwise a caller could rotate spoofed headers to dodge the limit.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))
        self._trust_proxy = os.getenv("TRUST_PROXY", "").lower() in {"1", "true", "yes"}
        self._requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Rate-limit GraphQL traffic before forwarding the request."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if request.url.path.startswith("/graphql") and request.method != "OPTIONS":
            client_key = self._get_rate_limit_key(request)
            if not await self._allow_request(client_key):
                body = {"detail": "Rate limit exceeded. Please try again later."}
                response = JSONResponse(status_code=429, content=body)
                response.headers["Retry-After"] = str(self.window_seconds)
                response.headers["X-RateLimit-Limit"] = str(self.max_requests)
                response.headers["X-RateLimit-Window"] = str(self.window_seconds)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)

    def _get_rate_limit_key(self, request: Request) -> str:
        """
        Return the per-caller budget key for a request.

        Authenticated requests are attributed to the validated JWT subject;
        the client IP is only used as a fallback (and never a spoofable
        ``X-Forwarded-For`` value unless ``TRUST_PROXY`` is enabled).
        """
        principal = getattr(request.state, "principal", None)
        if principal is not None and getattr(principal, "user_id", None):
            return f"user:{principal.user_id}"
        if self._trust_proxy:
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def _allow_request(self, client_key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            requests = self._requests[client_key]
            while requests and requests[0] <= cutoff:
                requests.popleft()

            if len(requests) >= self.max_requests:
                return False

            requests.append(now)
            return True
