"""
CORS and session middleware configuration.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from app.config.settings import settings
from app.config.logging import get_logger

logger = get_logger(__name__)


def setup_cors_middleware(app: FastAPI):
    """
    Configure CORS middleware with allowed origins.

    Args:
        app: FastAPI application instance
    """
    allowed_origins = list(settings.ALLOWED_ORIGINS)

    # Add derived frontend origin if not already present
    if settings.REACT_APP_URL not in allowed_origins:
        allowed_origins.append(settings.REACT_APP_URL)

    logger.info("CORS allowed origins: %s", allowed_origins)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
        expose_headers=["X-Total-Count"],
        max_age=3600,
    )
    
    # Security headers middleware
    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def setup_session_middleware(app: FastAPI):
    """
    Configure session middleware for OAuth state management.

    Args:
        app: FastAPI application instance
    """
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.APP_SECRET_KEY,
        session_cookie="gateway_session",
        max_age=settings.SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.SESSION_COOKIE_SECURE,
    )
    logger.debug("Session middleware configured")


def setup_middlewares(app: FastAPI):
    """
    Set up all middlewares for the application.

    Note: Middlewares are added in reverse order (last registered, first executed).

    Args:
        app: FastAPI application instance
    """
    # Add in reverse order: session first, then CORS
    setup_session_middleware(app)
    setup_cors_middleware(app)
