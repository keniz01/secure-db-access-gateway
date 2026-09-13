"""
Liveness and readiness endpoints for container orchestration and load balancers.

These routes are deliberately unauthenticated and dependency-free: probes must
never fail because of an expired session or a transient upstream.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness():
    """Return 200 while the process is alive. Touches no dependencies."""
    return {"status": "ok"}


@router.get("/readyz")
async def readiness():
    """Return 200 when the application is ready to serve traffic."""
    return {"status": "ready"}
