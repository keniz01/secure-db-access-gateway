"""Liveness and readiness endpoints for container orchestration and load balancers."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from config.app_logger import logger
from services.tenant_database_resolver import TenantDatabaseResolver

router = APIRouter(tags=["health"])


def _policy_enforcement_problem() -> str | None:
    """Return a reason string if the active policy evaluator is not enforcing.

    A process whose authorization layer would ALLOW everything is not ready to
    receive traffic, even though it can serve HTTP. The previous readiness check
    only validated the tenant database configuration, so a host with a
    misconfigured policy layer reported ready while authorizing every request.

    Scoped to production: in dev/test the operator has deliberately not
    configured a policy document, and /readyz is the Docker healthcheck for the
    whole stack (nginx and the other services gate on
    ``condition: service_healthy``). Failing there would break the documented
    local dev workflow for no security benefit. The loud RuntimeWarning raised by
    ``PolicyEvaluator.from_environment`` covers the non-production case.
    """
    from shared_secrets import is_environment_production

    if not is_environment_production():
        return None

    # Imported lazily: routes.sql_query_controller selects the evaluator at import
    # time, and importing it eagerly here would create a cycle via app_factory.
    from routes.sql_query_controller import _policy_evaluator
    from services.opa_policy_engine import OpaPolicyEvaluator
    from services.policy_engine import PolicyEvaluator

    if isinstance(_policy_evaluator, PolicyEvaluator) and not _policy_evaluator.enabled:
        # PolicyEvaluator(enabled=False) returns allowed=True for every request.
        return "policy evaluator is disabled (ALLOW-ALL); no policy document configured"

    if isinstance(_policy_evaluator, OpaPolicyEvaluator) and not _policy_evaluator.is_enabled:
        # Deny-all is fail-closed and therefore safe, but it means OPA is not
        # actually enforcing; report it so operators notice.
        logger.warning("Readiness: OPA evaluator is disabled; all queries will be denied")

    return None


@router.get("/healthz")
async def liveness() -> JSONResponse:
    """Return 200 while the process is alive. Touches no dependencies."""
    return JSONResponse(content={"status": "ok"})


@router.get("/readyz")
async def readiness() -> JSONResponse:
    """
    Return 200 only when the server-owned tenant database configuration is loadable
    and an enforcing authorization policy is in place.

    In production the gateway must never accept traffic while misconfigured.
    Checking here reuses the same validation the governed query path relies on,
    so orchestration can drain a misconfigured instance before routing work to it.
    """
    try:
        TenantDatabaseResolver.from_environment()
    except Exception as exc:  # noqa: BLE001 - any config failure makes the process unready
        logger.error("Readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Not ready") from exc

    try:
        problem = _policy_enforcement_problem()
    except Exception as exc:  # noqa: BLE001 - an unresolvable evaluator is not ready
        logger.error("Readiness check could not verify policy enforcement: %s", exc)
        raise HTTPException(status_code=503, detail="Not ready") from exc

    if problem is not None:
        logger.error("Readiness check failed: %s", problem)
        raise HTTPException(status_code=503, detail="Not ready")

    return JSONResponse(content={"status": "ready"})
