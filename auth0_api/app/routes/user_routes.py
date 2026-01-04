"""
User and dashboard routes.
"""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from app.config.logging import get_logger
from app.services.ai_service import get_ai_service
from app.schemas.responses import UserResponse, DashboardResponse, ErrorResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["user"])


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
            ).dict()
        )

    logger.debug("User info retrieved for: %s", user.get('email'))
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=user
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
            ).dict()
        )

    logger.info("Dashboard request for user: %s", user.get('email'))

    # Get AI-generated greeting
    message = None
    try:
        ai_service = get_ai_service()
        message = await ai_service.get_greeting(
            system="You are a helpful assistant.",
            user=f"In 1 line, generate a thoughtful informal quote of the day for {user.get('name')}."
        )
    except Exception as e:
        logger.warning("Failed to generate AI greeting: %s", e)

    # Use AI message or fallback
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
            message=final_message,
        ).dict()
    )
