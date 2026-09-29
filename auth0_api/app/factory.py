"""
Application factory for creating and configuring the FastAPI application.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config.settings import settings
from app.config.logging import configure_logging, get_logger
from app.middleware.setup import setup_middlewares
from app.routes import auth_routes, graphql_routes, health_routes, user_routes
from app.auth.session_store import validate_session_store
from app.routes.graphql_routes import _close_graphql_client

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    yield
    # Shutdown: close the GraphQL client
    await _close_graphql_client()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    # Configure logging
    configure_logging()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # Create FastAPI app with lifespan
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Auth API for SQL Query Executor platform",
        lifespan=lifespan,
    )

    # Validate session store config before wiring middlewares
    try:
        validate_session_store()
    except RuntimeError as exc:
        logger.error("Session store configuration error: %s", exc)
        raise

    # Setup middlewares (CORS, Sessions)
    setup_middlewares(app)

    # Include route blueprints
    app.include_router(health_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(user_routes.router)
    app.include_router(graphql_routes.router)

    logger.info("Application configured and ready")

    return app
