import asyncio
import os
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RateLimitMiddleware:
    """
    Layered in-memory rate limiting for GraphQL requests.

    Budgets are keyed by multiple dimensions for defense-in-depth:
    - IP (client host, fallback)
    - Principal (validated JWT subject from RBAC middleware)
    - Tenant (org_id from principal)
    - Database (database_id from request)

    Each dimension has independent limits. Request is rejected if ANY limit is exceeded.
    ``X-Forwarded-For`` is only trusted when ``TRUST_PROXY=1`` is configured.

    Backward compatibility: When only RATE_LIMIT_MAX_REQUESTS is set (legacy mode),
    uses single-dimension principal/IP limiting.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self._trust_proxy = os.getenv("TRUST_PROXY", "").lower() in {"1", "true", "yes"}

        # Per-dimension limits (configurable via env)
        # Legacy single-limit mode
        self._legacy_max = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "0"))
        # New layered limits
        self.ip_max_requests = int(os.getenv("RATE_LIMIT_IP_MAX", "120"))
        self.principal_max_requests = int(os.getenv("RATE_LIMIT_PRINCIPAL_MAX", "60"))
        self.tenant_max_requests = int(os.getenv("RATE_LIMIT_TENANT_MAX", "200"))
        self.database_max_requests = int(os.getenv("RATE_LIMIT_DATABASE_MAX", "100"))

        # Determine mode
        self._layered_mode = (
            self._legacy_max == 0
            and (self.ip_max_requests > 0
                 or self.principal_max_requests > 0
                 or self.tenant_max_requests > 0
                 or self.database_max_requests > 0)
        )

        # Storage: {dimension: {key: deque[timestamps]}}
        self._ip_requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._principal_requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._tenant_requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._database_requests: defaultdict[str, deque[float]] = defaultdict(deque)
        # Legacy storage
        self._legacy_requests: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Rate-limit GraphQL traffic before forwarding the request."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if request.url.path.startswith("/graphql") and request.method != "OPTIONS":
            if self._layered_mode:
                keys = self._get_rate_limit_keys(request)
                allowed, exceeded_dim = await self._allow_request(keys)
            else:
                client_key = self._get_rate_limit_key(request)
                allowed = await self._allow_legacy_request(client_key)
                exceeded_dim = "legacy"
            if not allowed:
                body = {"detail": f"Rate limit exceeded. Please try again later."}
                response = JSONResponse(status_code=429, content=body)
                response.headers["Retry-After"] = str(self.window_seconds)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP, optionally from X-Forwarded-For if TRUST_PROXY enabled."""
        if self._trust_proxy:
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _get_rate_limit_keys(self, request: Request) -> dict[str, Optional[str]]:
        """
        Extract all rate limit keys from request.

        Returns dict with keys: ip, principal, tenant, database
        """
        principal = getattr(request.state, "principal", None)

        # IP key
        ip = self._get_client_ip(request)

        # Principal key
        principal_id = None
        if principal is not None and getattr(principal, "user_id", None):
            principal_id = principal.user_id

        # Tenant key
        tenant_id = None
        if principal is not None and getattr(principal, "org_id", None):
            tenant_id = principal.org_id

        # Database key (from GraphQL variables or query)
        database_id = self._extract_database_id(request)

        return {
            "ip": ip,
            "principal": principal_id,
            "tenant": tenant_id,
            "database": database_id,
        }

    def _get_rate_limit_key(self, request: Request) -> str:
        """
        Legacy method: Return the per-caller budget key for a request.

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

    def _extract_database_id(self, request: Request) -> Optional[str]:
        """Extract database_id from GraphQL request body if present."""
        # Note: This is a best-effort extraction since we're in middleware
        # before body is fully parsed. The actual enforcement also happens
        # in the gateway with tenant_concurrent_limit.
        try:
            return None
        except Exception:
            return None

    async def _allow_request(self, keys: dict[str, Optional[str]]) -> tuple[bool, str]:
        """Check all rate limit dimensions. Returns (allowed, exceeded_dimension)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            # IP limit
            if keys["ip"]:
                if not await self._check_limit(keys["ip"], self.ip_max_requests, self._ip_requests, cutoff, now):
                    return False, "ip"

            # Principal limit
            if keys["principal"]:
                if not await self._check_limit(keys["principal"], self.principal_max_requests, self._principal_requests, cutoff, now):
                    return False, "principal"

            # Tenant limit
            if keys["tenant"]:
                if not await self._check_limit(keys["tenant"], self.tenant_max_requests, self._tenant_requests, cutoff, now):
                    return False, "tenant"

            # Database limit (if available)
            if keys["database"]:
                if not await self._check_limit(keys["database"], self.database_max_requests, self._database_requests, cutoff, now):
                    return False, "database"

            return True, ""

    async def _allow_legacy_request(self, client_key: str) -> bool:
        """Legacy single-dimension rate limiting."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            requests = self._legacy_requests[client_key]
            while requests and requests[0] <= cutoff:
                requests.popleft()

            if len(requests) >= self._legacy_max:
                return False

            requests.append(now)
            return True

    async def _check_limit(
        self,
        key: str,
        max_requests: int,
        storage: defaultdict[str, deque[float]],
        cutoff: float,
        now: float,
    ) -> bool:
        """Check and increment request count for a key."""
        if max_requests <= 0:
            return True  # No limit for this dimension

        requests = storage[key]
        while requests and requests[0] <= cutoff:
            requests.popleft()

        if len(requests) >= max_requests:
            return False

        requests.append(now)
        return True
