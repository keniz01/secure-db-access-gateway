import asyncio
import os
import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from fastapi import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware:
    """Simple in-memory rate limiting for high-volume GraphQL requests."""

    def __init__(self, app):
        self.app = app
        self.window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))
        self._requests: DefaultDict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if request.url.path.startswith("/graphql") and request.method != "OPTIONS":
            client_ip = self._get_client_ip(request)
            if not await self._allow_request(client_ip):
                body = {"detail": "Rate limit exceeded. Please try again later."}
                response = JSONResponse(status_code=429, content=body)
                response.headers["Retry-After"] = str(self.window_seconds)
                response.headers["X-RateLimit-Limit"] = str(self.max_requests)
                response.headers["X-RateLimit-Window"] = str(self.window_seconds)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)

    def _get_client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def _allow_request(self, client_ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            requests = self._requests[client_ip]
            while requests and requests[0] <= cutoff:
                requests.popleft()

            if len(requests) >= self.max_requests:
                return False

            requests.append(now)
            return True
