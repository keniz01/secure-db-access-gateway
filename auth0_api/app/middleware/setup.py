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
        allow_methods=["*"],
        allow_headers=["*"],
    )


def setup_session_middleware(app: FastAPI):
    """
    Configure session middleware for OAuth state management.

    Args:
        app: FastAPI application instance
    """
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.APP_SECRET_KEY
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
