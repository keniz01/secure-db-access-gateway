"""
User and dashboard routes.
"""

from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app.config.logging import get_logger
from app.config.settings import settings
from app.services.ai_service import get_ai_service
from app.services.text_to_sql_service import TextToSqlService
from app.schemas.responses import UserResponse, DashboardResponse, ErrorResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["user"])


async def fetch_usage_summary(org_id: str | None) -> dict:
    """Query Prometheus/Grafana for this org's usage metrics when available."""
    if not org_id:
        return {"queries_total": 0, "rows_returned_total": 0, "source": "no-org"}

    try:
        prom_query = f'sql_query_total{{org_id="{org_id}"}}'
        url = f"{settings.GRAFANA_PROMETHEUS_URL}/api/v1/query?query={quote(prom_query)}"
        response = httpx.get(url, timeout=5)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("data", {}).get("result", [])
        queries_total = 0
        if result:
            queries_total = int(float(result[0].get("value", [0, 0])[1]))
        rows_query = f'sql_query_rows_returned_bucket{{org_id="{org_id}"}}'
        rows_url = f"{settings.GRAFANA_PROMETHEUS_URL}/api/v1/query?query={quote(rows_query)}"
        rows_response = httpx.get(rows_url, timeout=5)
        rows_response.raise_for_status()
        rows_payload = rows_response.json()
        row_total = 0
        for item in rows_payload.get("data", {}).get("result", []):
            row_total += int(float(item.get("value", [0, 0])[1]))
        return {"queries_total": queries_total, "rows_returned_total": row_total, "source": "prometheus"}
    except Exception as exc:  # pragma: no cover - telemetry backends are optional
        logger.warning("Prometheus usage lookup failed for org %s: %s", org_id, exc)
        return {"queries_total": 0, "rows_returned_total": 0, "source": "unavailable"}


@router.get("/user", response_model=UserResponse)
async def get_user(request: Request):
    """
    Get authenticated user's information.

    Args:
        request: HTTP request with session

    Returns:
        User information or 401 if not authenticated
    """
    user = request.session.get('user')

    if not user:
        logger.info("Unauthenticated /user access attempt")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated"
            ).model_dump()
        )

    logger.debug("User info retrieved for: %s", user.get('email'))
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=user
    )


@router.get("/admin/overview")
async def get_admin_overview(request: Request):
    """Return a minimal admin overview for this org, backed by Prometheus/Grafana usage data."""
    user = request.session.get('user')
    if not user:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated"
            ).model_dump()
        )

    org_id = user.get('org_id') or request.query_params.get('org_id')
    usage = await fetch_usage_summary(org_id)
    orgs = list(settings.ORG_DB_CONNECTIONS.keys()) or ([org_id] if org_id else [])
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "org_id": org_id,
            "organizations": orgs,
            "usage": usage,
            "db_connections": list(settings.ORG_DB_CONNECTIONS.keys()),
            "source": "prometheus" if usage.get("source") == "prometheus" else "config",
        },
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(request: Request):
    """
    Get dashboard with user information and AI-generated greeting.

    Args:
        request: HTTP request with session

    Returns:
        Dashboard data with greeting message or 401 if not authenticated
    """
    user = request.session.get('user')

    if not user:
        logger.info("Unauthenticated dashboard access attempt")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated"
            ).model_dump()
        )

    logger.info("Dashboard request for user: %s", user.get('email'))

    # Get AI-generated greeting when enabled, falling back gracefully when the feature is disabled or unavailable.
    message = None
    if settings.ENABLE_AI_GREETING:
        try:
            ai_service = get_ai_service()
            message = await ai_service.get_greeting(
                system="You are a helpful assistant.",
                user=f"In 1 line, generate a thoughtful informal quote of the day for {user.get('name')}."
            )
        except Exception as e:
            logger.warning("Failed to generate AI greeting: %s", e)

    final_message = (
        message.strip()
        if isinstance(message, str) and message.strip()
        else "You are successfully authenticated."
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=DashboardResponse(
            email=user.get("email"),
            name=user.get("name"),
            org_id=user.get("org_id"),
            message=final_message,
        ).model_dump()
    )


class TextToSqlRequest(BaseModel):
    """Request model for text-to-SQL conversion."""
    query: str
    execute: bool = False


@router.post("/text-to-sql")
async def text_to_sql(request: Request, body: TextToSqlRequest):
    """
    Convert natural language query to SQL and optionally execute it.

    Args:
        request: HTTP request with session
        body: Request body containing natural language query

    Returns:
        Generated SQL and optionally query results, or 401 if not authenticated
    """
    user = request.session.get('user')

    if not user:
        logger.info("Unauthenticated text-to-sql access attempt")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated"
            ).model_dump()
        )

    # Validate input
    if not body.query or not body.query.strip():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Query cannot be empty"
            ).model_dump()
        )

    logger.info("Text-to-SQL request from user: %s", user.get('email'))

    try:
        # Initialize services
        ai_service = get_ai_service()
        text_to_sql_service = TextToSqlService(ai_service)

        # Generate SQL from natural language
        result = await text_to_sql_service.generate_sql_from_text(
            query=body.query.strip(),
            execute=body.execute
        )

        if "error" in result:
            # Check if it's a validation error (400) or server error (500)
            error_msg = result["error"]
            is_validation_error = "validation" in error_msg.lower() or "SQL validation" in error_msg
            status_code = status.HTTP_400_BAD_REQUEST if is_validation_error else status.HTTP_500_INTERNAL_SERVER_ERROR
            
            return JSONResponse(
                status_code=status_code,
                content={
                    "error": error_msg,
                    "sql": result.get("sql"),
                }
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "sql": result.get("sql"),
                "results": result.get("results") if body.execute else None,
                "schema": result.get("schema"),
            }
        )

    except ValueError as e:
        # Handle validation errors from sql_query_api
        logger.warning("SQL validation error in text-to-sql endpoint: %s", e)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            ).model_dump()
        )
    except Exception as e:
        logger.exception("Error in text-to-sql endpoint: %s", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process text-to-SQL request: {str(e)}"
            ).model_dump()
        )
