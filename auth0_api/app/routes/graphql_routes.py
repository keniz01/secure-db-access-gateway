"""Authenticated backend-for-frontend proxy for the SQL GraphQL API."""

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from app.config.logging import get_current_correlation_id
from app.config.settings import settings
from app.routes.user_routes import get_authenticated_session
from app.schemas.responses import ErrorResponse
from app.security.csrf import csrf_failure_reason
from app.utils.mtls_client import create_mtls_client

router = APIRouter(prefix="/api", tags=["graphql"])


# Module-level client for connection pooling
_graphql_client: httpx.AsyncClient | None = None


async def _get_graphql_client() -> httpx.AsyncClient:
    """Get or create the GraphQL client with mTLS support."""
    global _graphql_client
    if _graphql_client is None or _graphql_client.is_closed:
        _graphql_client = create_mtls_client(
            base_url=settings.SQL_QUERY_API_URL,
            timeout=30.0,
        )
    return _graphql_client


async def _close_graphql_client() -> None:
    """Close the GraphQL client."""
    global _graphql_client
    if _graphql_client and not _graphql_client.is_closed:
        await _graphql_client.aclose()
        _graphql_client = None


@router.post("/graphql")
async def proxy_graphql(request: Request):
    """Forward GraphQL using the access token kept in the server-side session."""
    session = await get_authenticated_session(request)
    access_token = session.get("access_token") if session else None
    if not isinstance(access_token, str) or not access_token:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=ErrorResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        ).model_dump())

    reason = csrf_failure_reason(request, session)
    if reason:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=ErrorResponse(
            status_code=status.HTTP_403_FORBIDDEN, detail=reason
        ).model_dump())

    cid = getattr(getattr(request, "state", None), "correlation_id", None) or get_current_correlation_id()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": request.headers.get("content-type", "application/json"),
    }
    if cid and cid != "N/A":
        headers["X-Correlation-ID"] = cid
        headers["X-Request-ID"] = cid

    client = await _get_graphql_client()
    upstream = await client.post(
        settings.SQL_QUERY_API_URL,
        content=await request.body(),
        headers=headers,
    )
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))
