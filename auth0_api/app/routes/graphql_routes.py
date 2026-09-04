"""Authenticated backend-for-frontend proxy for the SQL GraphQL API."""

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from app.config.settings import settings
from app.routes.user_routes import get_authenticated_session
from app.schemas.responses import ErrorResponse

router = APIRouter(prefix="/api", tags=["graphql"])


@router.post("/graphql")
async def proxy_graphql(request: Request):
    """Forward GraphQL using the access token kept in the server-side session."""
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=ErrorResponse(
            status_code=status.HTTP_403_FORBIDDEN, detail="CSRF protection failed"
        ).model_dump())

    session = get_authenticated_session(request)
    access_token = session.get("access_token") if session else None
    if not isinstance(access_token, str) or not access_token:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=ErrorResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        ).model_dump())

    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.post(
            settings.SQL_QUERY_API_URL,
            content=await request.body(),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": request.headers.get("content-type", "application/json"),
            },
        )
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))
