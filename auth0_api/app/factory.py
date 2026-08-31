"""
Application factory for creating and configuring the FastAPI application.
"""

from fastapi import FastAPI
from app.config.settings import settings
from app.config.logging import configure_logging, get_logger
from app.middleware.setup import setup_middlewares
from app.routes import auth_routes, graphql_routes, user_routes

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    # Configure logging
    configure_logging()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # Create FastAPI app
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Auth API for SQL Query Executor platform",
    )

    # Setup middlewares (CORS, Sessions)
    setup_middlewares(app)

    # Include route blueprints
    app.include_router(auth_routes.router)
    app.include_router(user_routes.router)
    app.include_router(graphql_routes.router)

    logger.info("Application configured and ready")

    return app
