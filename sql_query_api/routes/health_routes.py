"""Liveness and readiness endpoints for container orchestration and load balancers."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from config.app_logger import logger
from services.tenant_database_resolver import TenantDatabaseResolver

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> JSONResponse:
    """Return 200 while the process is alive. Touches no dependencies."""
    return JSONResponse(content={"status": "ok"})


@router.get("/readyz")
async def readiness() -> JSONResponse:
    """
    Return 200 only when the server-owned tenant database configuration is loadable.

    In production the gateway must never accept traffic while misconfigured.
    Checking here reuses the same validation the governed query path relies on,
    so orchestration can drain a misconfigured instance before routing work to it.
    """
    try:
        TenantDatabaseResolver.from_environment()
    except Exception as exc:  # noqa: BLE001 - any config failure makes the process unready
        logger.error("Readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Not ready") from exc
    return JSONResponse(content={"status": "ready"})
