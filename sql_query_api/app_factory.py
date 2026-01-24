"""
Application factory for creating and configuring the FastAPI application.
"""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from middlewares.logging_middleware import LoggingMiddleware
from middlewares.correlation_middleware import correlation_id_middleware
from exceptions.exception_handlers import (
    http_exception_handler,
    validation_exception_handler,
)
from graphql_schema.schema import schema
from strawberry.fastapi import GraphQLRouter
from config.app_logger import logger
import os
from typing import List


def setup_cors_middleware(app: FastAPI):
    """
    Configure CORS middleware with allowed origins.

    Args:
        app: FastAPI application instance
    """
    # CORS Configuration - Restrict to configured origins
    cors_origins_env = os.getenv("CORS_ORIGINS", "")
    if cors_origins_env:
        # Production: Load from environment
        origins: List[str] = [origin.strip() for origin in cors_origins_env.split(",")]
    else:
        # Development: Default to localhost
        origins = [
            "http://localhost:5173",  # Vite dev server
            "http://localhost:3000",  # Alternative port
            "http://127.0.0.1:5173",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
        expose_headers=["X-Total-Count"],
        max_age=3600,
    )


def setup_security_middleware(app: FastAPI):
    """
    Configure security headers middleware.

    Args:
        app: FastAPI application instance
    """
    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def setup_custom_middlewares(app: FastAPI):
    """
    Configure custom middlewares.

    Args:
        app: FastAPI application instance
    """
    app.add_middleware(LoggingMiddleware)
    app.middleware("http")(correlation_id_middleware)


def setup_exception_handlers(app: FastAPI):
    """
    Configure exception handlers.

    Args:
        app: FastAPI application instance
    """
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)


def setup_routes(app: FastAPI):
    """
    Configure routes and routers.

    Args:
        app: FastAPI application instance
    """
    # GraphQL
    graphql_router = GraphQLRouter(schema)
    app.include_router(graphql_router, prefix="/graphql")


async def lifespan(app: FastAPI):
    """
    Application lifespan events.
    """
    logger.info("🚀 Starting FastAPI application...")
    yield
    logger.info("🛑 Shutting down FastAPI application...")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    # Create FastAPI app
    app = FastAPI(
        title="Postgres SQL API",
        version="1.0.0",
        lifespan=lifespan
    )

    # Setup middlewares
    setup_cors_middleware(app)
    setup_security_middleware(app)
    setup_custom_middlewares(app)

    # Setup exception handlers
    setup_exception_handlers(app)

    # Setup routes
    setup_routes(app)

    logger.info("Application configured and ready")

    return app